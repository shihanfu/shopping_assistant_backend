ARG REPO=248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu
ARG BASE_TAG=base
FROM ${REPO}:${BASE_TAG}

ARG ROLLOUT_ENGINE

RUN condax install uv

ENV LANG=en_US.UTF-8
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install python3-dev cmake -y && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY pyproject.toml /workdir/pyproject.toml
COPY uv.lock /workdir/uv.lock
RUN python3.10 -m pip install --upgrade pip setuptools
RUN cd /workdir && uv sync
ENV CUDNN_PATH=/workdir/.venv/lib/python3.10/site-packages/nvidia/cudnn
RUN cd /workdir && uv sync --extra compile --extra $ROLLOUT_ENGINE # two stages as first stage install build dependencies.
RUN cd /workdir && uv run wandb login 5f979adf061882b2252d23ea8472a6fb3c492565

COPY . /workdir


WORKDIR /workdir
