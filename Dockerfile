FROM python:3.7

COPY . /app
WORKDIR /app
RUN python3 setup.py install

