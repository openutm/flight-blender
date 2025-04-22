
FROM --platform=linux/amd64 python:3.12-slim
ENV PYTHONUNBUFFERED=1


RUN mkdir /app
WORKDIR /app
COPY requirements.txt /app/
RUN pip install -r requirements.txt
RUN rm -rf __pycache__
COPY . /app/


# FROM --platform=linux/amd64 python:3.12-slim

# ENV PYTHONUNBUFFERED=1
# ENV PYTHONDONTWRITEBYTECODE=1

# WORKDIR /app

# COPY requirements.txt .
# RUN pip install -U pip && pip install -r requirements.txt

# RUN addgroup --gid 10000 django  && adduser --shell /bin/bash --disabled-password --gecos "" --uid 10000 --ingroup django django
# RUN chown -R django:django /app
# USER django:django

# COPY --chown=django:django . .

# EXPOSE 8000
