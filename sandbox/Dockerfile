# The image model-written code runs inside. Deliberately boring and small.
#
# Nothing is installed that the generated code does not need. No curl, no git,
# no shell utilities beyond what the base image already carries. Every extra
# binary here is one more thing available to code you did not write.
#
# Build once at deploy time, not per request:
#     docker build -t agent-sandbox:latest sandbox/

FROM python:3.13-slim

# --no-cache-dir keeps the image small; pandas is the only thing the preamble
# imports, so it is the only thing installed.
RUN pip install --no-cache-dir pandas==3.0.5

# A home the "nobody" user can nominally point at. The container runs read-only,
# so nothing is written here -- this exists so libraries that look up a home
# directory do not error out on a missing path.
ENV HOME=/tmp
WORKDIR /tmp

# The container is also given --user 65534:65534 at run time. Setting it here as
# well means an accidental `docker run` without that flag is still not root.
USER 65534:65534
