FROM 248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:base


RUN condax install uv

COPY . /workdir

WORKDIR /workdir
