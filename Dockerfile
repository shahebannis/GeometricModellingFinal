# FROM ubuntu:22.04

# ENV DEBIAN_FRONTEND=noninteractive

# RUN apt-get update && apt-get install -y \
#     build-essential \
#     cmake \
#     git \
#     libpng-dev \
#     zlib1g-dev \
#     libbz2-dev \
#     python3 \
#     python3-pip \
#     && rm -rf /var/lib/apt/lists/*

# # G-PCC - tmc13
# RUN git clone --depth 1 \
#         https://github.com/MPEGGroup/mpeg-pcc-tmc13.git \
#         /opt/tmc13

# WORKDIR /opt/tmc13
# RUN mkdir build \
#     && cd build \
#     && cmake .. -DCMAKE_BUILD_TYPE=Release \
#     && make -j"$(nproc)"

# RUN BIN=$(find /opt/tmc13/build -type f -name "tmc3" | head -1) \
#     && if [ -z "$BIN" ]; then echo "ERROR: tmc3 not built"; exit 1; fi \
#     && install -m 755 "$BIN" /usr/local/bin/tmc3 \
#     && echo "tmc3 OK: $BIN"

# # HM
# RUN git clone --depth 1 \
#         https://vcgit.hhi.fraunhofer.de/jct-vc/HM.git \
#         /opt/hm

# WORKDIR /opt/hm
# # Apply the SCM patch that TMC2 needs (stored in tmc2 dependencies)
# # Skip the patch if tmc2 isn't cloned yet -- we'll apply it after
# RUN make -j"$(nproc)" -f Makefile TAppEncoder TAppDecoder 2>/dev/null \
#     || (cd build/linux && cmake ../.. -DCMAKE_BUILD_TYPE=Release && make -j"$(nproc)") \
#     || echo "Trying alternate build..."

# # If neither worked, use the linux makefile directly
# RUN if [ ! -f /opt/hm/bin/TAppEncoderStatic ] && [ ! -f /opt/hm/bin/TAppEncoder ]; then \
#         cd /opt/hm && \
#         mkdir -p build/linux && \
#         cd build/linux && \
#         cmake ../.. -DCMAKE_BUILD_TYPE=Release && \
#         make -j"$(nproc)"; \
#     fi

# RUN find /opt/hm -name "TAppEncoder*" -o -name "TAppDecoder*" | grep -v "\.cpp\|\.h\|\.txt" \
#     && echo "HM build complete"

# # V-PCC - tmc2
# RUN git clone --depth 1 \
#         https://github.com/MPEGGroup/mpeg-pcc-tmc2.git \
#         /opt/tmc2

# WORKDIR /opt/tmc2
# RUN bash build.sh

# # Install PccApp binaries
# RUN ENC=$(find /opt/tmc2 -name "PccAppEncoder" -type f | head -1) \
#     && DEC=$(find /opt/tmc2 -name "PccAppDecoder" -type f | head -1) \
#     && if [ -z "$ENC" ] || [ -z "$DEC" ]; then echo "ERROR: PccApp not built"; exit 1; fi \
#     && install -m 755 "$ENC" /usr/local/bin/PccAppEncoder \
#     && install -m 755 "$DEC" /usr/local/bin/PccAppDecoder \
#     && echo "PccAppEncoder: $ENC" && echo "PccAppDecoder: $DEC"

# # Show HM binary paths so pc_codecs.py can find them
# RUN echo "=== HM binaries ===" \
#     && find /opt/hm -type f \( -name "TAppEncoder*" -o -name "TAppDecoder*" \) \
#        -not -name "*.cpp" -not -name "*.h" -not -name "*.txt" \
#        -not -name "*.vcxproj*"

# # Draco
# RUN git clone --depth 1 https://github.com/google/draco.git /opt/draco \
#     && mkdir /opt/draco/build \
#     && cd /opt/draco/build \
#     && cmake .. -DCMAKE_BUILD_TYPE=Release -DDRACO_POINT_CLOUD_COMPRESSION=ON \
#     && make -j"$(nproc)" \
#     && install -m 755 draco_encoder /usr/local/bin/draco_encoder \
#     && install -m 755 draco_decoder /usr/local/bin/draco_decoder \
#     && echo "Draco OK"

# WORKDIR /app
# COPY requirements.txt .
# RUN pip3 install --no-cache-dir -r requirements.txt

# COPY preprocessing.py evaluation.py pc_codecs.py run_experiment.py ./
# COPY app/ ./app/

# EXPOSE 5000
# VOLUME ["/data", "/results"]

# ENTRYPOINT ["python3", "run_experiment.py"]
# CMD ["--synthetic"]

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    python3 \
    python3-pip \
    libboost-all-dev \
    libeigen3-dev \
    libtbb-dev \
    pkg-config \
    ca-certificates \
    draco \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# TMC3 / G-PCC
RUN git clone https://github.com/MPEGGroup/mpeg-pcc-tmc13.git /opt/tmc3 && \
    cd /opt/tmc3 && \
    mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j"$(nproc)"

# Find the real tmc3 binary and link it
RUN set -eux; \
    TMC3_BIN="$(find /opt/tmc3 -type f -name tmc3 | head -n 1 || true)"; \
    echo "TMC3_BIN=$TMC3_BIN"; \
    [ -n "$TMC3_BIN" ] && ln -sf "$TMC3_BIN" /usr/local/bin/tmc3; \
    command -v tmc3; \
    command -v draco_encoder; \
    command -v draco_decoder

ENV PATH="/usr/local/bin:${PATH}"
ENV TMC3_BINARY=/usr/local/bin/tmc3
ENV DRACO_ENCODER=/usr/bin/draco_encoder
ENV DRACO_DECODER=/usr/bin/draco_decoder

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY preprocessing.py evaluation.py pc_codecs.py ./
COPY app/ ./app/

EXPOSE 5000
VOLUME ["/data", "/results"]

CMD ["python3", "app/app.py"]