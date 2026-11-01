# syntax=docker/dockerfile:1
# Preserve the application's PG minor and volume format; add only vector files.
FROM postgres:16.8-alpine AS vector-build
ADD --checksum=sha256:cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85 https://codeload.github.com/pgvector/pgvector/tar.gz/refs/tags/v0.8.7 /tmp/vector.tar.gz
RUN apk add --no-cache build-base clang19 llvm19-dev \
    && tar -xzf /tmp/vector.tar.gz -C /tmp \
    && cd /tmp/pgvector-0.8.7 \
    && make OPTFLAGS="" \
    && make install

FROM postgres:16.8-alpine
COPY --from=vector-build /usr/local/lib/postgresql/vector.so /usr/local/lib/postgresql/vector.so
COPY --from=vector-build /usr/local/lib/postgresql/bitcode/vector /usr/local/lib/postgresql/bitcode/vector
COPY --from=vector-build /usr/local/lib/postgresql/bitcode/vector.index.bc /usr/local/lib/postgresql/bitcode/vector.index.bc
COPY --from=vector-build /usr/local/share/postgresql/extension/vector* /usr/local/share/postgresql/extension/
COPY --from=vector-build /tmp/pgvector-0.8.7/LICENSE /usr/share/doc/pgvector/LICENSE
