import base64
import io
import json

TEST_URL_ANTHROPIC = 'https://api.anthropic.com/v1/models'


def check_proxy_alive(proxy_url, target_url):
    import httpx

    try:
        with httpx.Client(proxy=proxy_url, timeout=8) as client:
            client.get(target_url)
    except Exception as exc:
        return str(exc)
    return None


def extract_text_from_pdf(pdf_bytes):
    import pdfplumber

    parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or '')
    return '\n'.join(parts)


MAX_VISION_IMAGE_BYTES = 4_500_000  # запас под лимит Bedrock/Claude в 5 242 880 байт на изображение


def render_pdf_pages_as_png(pdf_bytes, max_pages=5, zoom=3.0):
    import fitz  # PyMuPDF

    images = []
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        for page in doc[:max_pages]:
            images.append(_render_page_within_limit(page, zoom))
    finally:
        doc.close()
    return images


def _render_page_within_limit(page, zoom):
    import fitz  # PyMuPDF

    # Скан-фото (особенно с телефона) на zoom=3.0 иногда превышает лимит
    # вложения в 5 МБ (реальный случай: 7.83 МБ на P00003).
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    png_bytes = pix.tobytes('png')
    if len(png_bytes) <= MAX_VISION_IMAGE_BYTES:
        return png_bytes

    # Раньше здесь сразу уменьшалось разрешение - но это напрямую портит
    # мелкий текст и может привести к тому, что ИИ начнёт путать цифры/буквы
    # (реальный случай 2026-07-15: УПД №58 от Асадуллина, PNG на zoom=3.0
    # весил 5.87 МБ, после урезания до zoom=2.1 модель полностью придумала
    # продавца, ИНН и номер документа). JPEG на ТОМ ЖЕ разрешении обычно
    # даёт кратно меньший размер почти без видимой потери чёткости (тот же
    # файл: JPEG q=95 на исходном zoom - всего 1.47 МБ), поэтому пробуем
    # сжатие в JPEG на полном разрешении, прежде чем вообще трогать zoom.
    for quality in (95, 85, 75):
        jpg_bytes = pix.tobytes('jpg', jpg_quality=quality)
        if len(jpg_bytes) <= MAX_VISION_IMAGE_BYTES:
            return jpg_bytes

    # Даже сжатый JPEG на полном разрешении не помещается (огромная
    # страница/очень богатая текстура) - только тогда уменьшаем само
    # разрешение, как последний вариант.
    current_zoom = zoom
    for _ in range(6):
        current_zoom *= 0.7
        pix = page.get_pixmap(matrix=fitz.Matrix(current_zoom, current_zoom))
        jpg_bytes = pix.tobytes('jpg', jpg_quality=75)
        if len(jpg_bytes) <= MAX_VISION_IMAGE_BYTES:
            return jpg_bytes
    return jpg_bytes


def pdf_is_scanned(pdf_bytes, image_area_ratio=0.5):
    """True if most pages are essentially one big embedded photo/scan.

    Such PDFs (e.g. a phone photo of a paper document) often carry a hidden
    OCR text layer of unknown, sometimes very poor, quality - pdfplumber can
    return non-empty but garbled text for them. Detecting the page layout
    itself (one large raster image covering the page) is far more reliable
    than trying to judge whether extracted text "looks right", so scanned
    pages always go through the vision path regardless of any text layer.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        if len(doc) == 0:
            return False
        scanned_pages = 0
        for page in doc:
            page_area = page.rect.width * page.rect.height
            if page_area <= 0:
                continue
            covered = 0.0
            for img in page.get_images(full=True):
                for bbox in page.get_image_rects(img[0]):
                    covered += bbox.width * bbox.height
            if covered / page_area >= image_area_ratio:
                scanned_pages += 1
        return scanned_pages >= max(1, len(doc) // 2)
    finally:
        doc.close()


def parse_json_relaxed(content):
    content = content.strip()
    if content.startswith('```'):
        content = content.strip('`')
        if content.lower().startswith('json'):
            content = content[4:]
    return json.loads(content.strip())


def anthropic_client(api_key, proxy_url):
    import anthropic

    if proxy_url:
        return anthropic.Anthropic(
            api_key=api_key,
            http_client=anthropic.DefaultHttpxClient(proxy=proxy_url),
        )
    return anthropic.Anthropic(api_key=api_key)


def openai_client(api_key, base_url, proxy_url):
    from openai import OpenAI

    if proxy_url:
        import httpx
        return OpenAI(api_key=api_key, base_url=base_url, http_client=httpx.Client(proxy=proxy_url))
    return OpenAI(api_key=api_key, base_url=base_url)


def call_claude(api_key, model, prompt, schema, proxy_url=None):
    client = anthropic_client(api_key, proxy_url)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(response.content[0].text)


def _image_media_type(image_bytes):
    # _render_page_within_limit может вернуть JPEG вместо PNG, если даже
    # уменьшенный PNG не помещается в лимит размера - определяем реальный
    # формат по сигнатуре, а не считаем его всегда PNG.
    if image_bytes[:2] == b'\xff\xd8':
        return 'image/jpeg'
    return 'image/png'


def call_claude_vision(api_key, model, images_png, prompt, schema, proxy_url=None):
    client = anthropic_client(api_key, proxy_url)
    content = []
    for png_bytes in images_png:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _image_media_type(png_bytes),
                "data": base64.b64encode(png_bytes).decode(),
            },
        })
    content.append({"type": "text", "text": prompt})

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    return json.loads(response.content[0].text)


def call_openai_compatible(api_key, base_url, model, prompt, proxy_url=None):
    client = openai_client(api_key, base_url, proxy_url)
    messages = [{"role": "user", "content": prompt}]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(model=model, messages=messages)
    return parse_json_relaxed(response.choices[0].message.content)


def call_openai_compatible_vision(api_key, base_url, model, images_png, prompt, proxy_url=None):
    client = openai_client(api_key, base_url, proxy_url)
    content = []
    for png_bytes in images_png:
        b64 = base64.b64encode(png_bytes).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{_image_media_type(png_bytes)};base64,{b64}"},
        })
    content.append({"type": "text", "text": prompt})

    messages = [{"role": "user", "content": content}]
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception:
        response = client.chat.completions.create(model=model, messages=messages)
    return parse_json_relaxed(response.choices[0].message.content)


def call_llm(api_key, model, base_url, prompt_plain, prompt_structured, schema, proxy_url=None):
    if base_url:
        return call_openai_compatible(api_key, base_url, model, prompt_plain, proxy_url)
    return call_claude(api_key, model, prompt_structured, schema, proxy_url)


def call_llm_vision(api_key, model, base_url, images_png, prompt_plain, prompt_structured, schema, proxy_url=None):
    if base_url:
        return call_openai_compatible_vision(api_key, base_url, model, images_png, prompt_plain, proxy_url)
    return call_claude_vision(api_key, model, images_png, prompt_structured, schema, proxy_url)
