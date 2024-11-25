DEFAULT_GOAL := help
PROJECT=kent
DOCKER_USER=pabloparadaroca
TAG=${PROJECT}:latest
IMAGE_NAME=${DOCKER_USER}/${TAG}

.PHONY: help
help:
	@echo "Available rules:"
	@echo ""
	@fgrep -h "##" Makefile | fgrep -v fgrep | sed 's/\(.*\):.*##/\1:  /'

.PHONY: build
build: clean lint test  ## Build sdist and wheel for distribution
	check-manifest
	python -m build
	echo ""
	echo "Run: git tag -s TAGNAME"
	echo ""
	echo "Run: twine upload -r kent dist/*"
	echo ""
	echo "Push tag to GitHub."

.PHONY: test
test:  ## Run tests and static typechecking
	tox

.PHONY: format
format:  ## Format files
	tox exec -e py39-lint -- ruff format

.PHONY: lint
lint:  ## Lint files
	tox -e py39-lint

.PHONY: clean
clean:  ## Clean build artifacts
	rm -rf build dist src/${PROJECT}.egg-info .tox .pytest_cache
	find src/ -name __pycache__ | xargs rm -rf
	find src/ -name '*.pyc' | xargs rm -rf

.PHONY: testdocker
testdocker:  ## Build Docker image and run it
	docker build --no-cache -t kent:latest .
	docker run --init --rm --publish 14765:14765 kent:latest run --host 0.0.0.0 --port 14765

.PHONY: publish
publish:  ## Build Docker image and run it
	docker build -t ${IMAGE_NAME} . 
	docker push ${IMAGE_NAME}
