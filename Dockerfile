FROM        ubuntu:14.04

# Last build date - this can be updated whenever there are security updates so
# that everything is rebuilt
ENV         security_updates_as_of 2015-08-09

ADD         . /app

# Install security updates and required packages
RUN         apt-get -qy update && \
            apt-get -y install apt-transport-https software-properties-common && \
            add-apt-repository -y "deb https://clusterhq-archive.s3.amazonaws.com/ubuntu/$(lsb_release --release --short)/\$(ARCH) /" && \
            apt-get -qy update && \
            apt-get -qy upgrade && \
            apt-get -qy install python-pip python-dev libyaml-dev libffi-dev libssl-dev && \
            pip install twisted==14.0.0 treq==0.2.1 service_identity pycrypto pyrsistent pyyaml==3.10 && \
            cd /app && pip install . && \
            apt-get remove --purge -y $(apt-mark showauto) && \
            apt-get -y install apt-transport-https software-properties-common && \
            apt-get -y --force-yes install python python-pip clusterhq-flocker-cli && \
            rm -rf /var/lib/apt/lists/*

WORKDIR     /pwd
