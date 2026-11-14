REGISTRY   ?= registry.local:5000
VERSION    ?= $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
IMAGE      := $(REGISTRY)/standbild
# z. B. BUILD_ARGS="--build-arg CUDA=1" oder "--build-arg YTDLP_VERSION=2026.10.26"
BUILD_ARGS ?=

.PHONY: build push test lint

build:
	docker build $(BUILD_ARGS) -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

push: build
	docker push $(IMAGE):$(VERSION)
	docker push $(IMAGE):latest

test:
	pytest

lint:
	ruff check src tests
	ruff format --check src tests
	mypy
