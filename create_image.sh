#!/bin/bash

DOCKER_IMAGE_NAME="lnplay/cln:v24.05"
docker buildx build -t "$DOCKER_IMAGE_NAME" . --load
docker push "$DOCKER_IMAGE_NAME"