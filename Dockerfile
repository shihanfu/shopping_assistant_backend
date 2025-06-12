FROM 248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:base


RUN condax install uv

ENV LANG=en_US.UTF-8
RUN DEBIAN_FRONTEND=noninteractive  apt-get install python3-dev -y

COPY . /workdir


WORKDIR /workdir
