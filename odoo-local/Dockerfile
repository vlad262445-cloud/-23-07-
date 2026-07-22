FROM odoo:18.0
USER root
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed typing_extensions \
    && pip3 install --no-cache-dir --break-system-packages \
    pdfplumber \
    pymupdf \
    anthropic \
    openai \
    httpx
USER odoo
