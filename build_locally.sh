#!/usr/bin/env bash

APP=flight-blender

docker build --platform linux/amd64 -t "openskiessh/$APP" .
