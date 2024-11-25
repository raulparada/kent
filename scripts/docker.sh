#!/usr/bin/env bash

DOCKER_USER=pabloparadaroca
TAG=kent:beta
IMAGE_NAME="$DOCKER_USER/$TAG"

docker build -t $IMAGE_NAME . 
docker push $IMAGE_NAME
