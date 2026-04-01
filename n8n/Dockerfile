# Stage 1: Build Python + deps in a standard Alpine image
FROM python:3.12-alpine AS python-build

RUN pip install --no-cache-dir \
    openai>=1.30.0 \
    pydantic>=2.5.0 \
    python-dotenv>=1.0.0 \
    Pillow>=10.0.0

# Stage 2: n8n image with Python copied in
FROM n8nio/n8n:latest

USER root

# Copy Python runtime and installed packages from build stage
COPY --from=python-build /usr/local/bin/python3 /usr/local/bin/python3
COPY --from=python-build /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=python-build /usr/local/lib/libpython3.12.so* /usr/local/lib/
COPY --from=python-build /usr/lib/libffi* /usr/lib/

# Ensure shared libs are found
RUN ldconfig /usr/local/lib 2>/dev/null || true
RUN ln -sf /usr/local/bin/python3 /usr/local/bin/python

USER node
