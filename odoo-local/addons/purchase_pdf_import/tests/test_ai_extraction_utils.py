import os

from odoo.tests.common import TransactionCase, tagged

from ..wizard import ai_extraction_utils as aeu


@tagged('post_install', '-at_install')
class TestAiExtractionUtils(TransactionCase):
    """Ольга Скрынник не смогла импортировать УПД по P00003 - ИИ API вернул
    400, т.к. отрендеренный PNG скан-фото весил 7.83 МБ при лимите Bedrock
    в 5 242 880 байт. render_pdf_pages_as_png должен сам подстраивать
    разрешение под лимит вместо падения с ошибкой у пользователя.
    """

    def _noisy_pdf_bytes(self, width_pt=350, height_pt=500, zoom=3.0):
        # Шум должен быть уже в целевом разрешении рендера - иначе
        # PyMuPDF при масштабировании его сглаживает интерполяцией, и PNG
        # получается компактным независимо от исходного размера страницы
        # (не воспроизводит реальный случай плотного фото-скана).
        import fitz  # PyMuPDF

        width_px, height_px = int(width_pt * zoom), int(height_pt * zoom)
        samples = os.urandom(width_px * height_px * 3)
        pix = fitz.Pixmap(fitz.csRGB, width_px, height_px, samples, False)
        doc = fitz.open()
        page = doc.new_page(width=width_pt, height=height_pt)
        page.insert_image(page.rect, pixmap=pix)
        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes

    def test_render_downscales_oversized_page(self):
        pdf_bytes = self._noisy_pdf_bytes()
        images = aeu.render_pdf_pages_as_png(pdf_bytes, max_pages=1)
        self.assertEqual(len(images), 1)
        self.assertLessEqual(
            len(images[0]), aeu.MAX_VISION_IMAGE_BYTES,
            'страница должна уместиться в лимит размера изображения ИИ API, а не падать с 400')

    def test_media_type_matches_actual_bytes(self):
        # Если даже уменьшенный PNG не уложился в лимит и рендер перешёл на
        # JPEG (см. render_pdf_pages_as_png) - media_type в запросе к API
        # должен отражать реальный формат байтов, а не быть всегда image/png.
        self.assertEqual(aeu._image_media_type(b'\x89PNG\r\n\x1a\n...'), 'image/png')
        self.assertEqual(aeu._image_media_type(b'\xff\xd8\xff\xe0...'), 'image/jpeg')
