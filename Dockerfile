FROM 248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:base


RUN condax install uv

ENV LANG=en_US.UTF-8
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install python3-dev cmake -y && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY pyproject.toml /workdir/pyproject.toml
COPY uv.lock /workdir/uv.lock
RUN python3.10 -m pip install --upgrade pip setuptools
RUN cd /workdir && uv sync
ENV CUDNN_PATH=/workdir/.venv/lib/python3.10/site-packages/nvidia/cudnn
RUN cd /workdir && uv sync --extra compile # two stages as first stage install build dependencies.


COPY . /workdir


WORKDIR /workdir
