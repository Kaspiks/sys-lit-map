FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PYTHONPATH=/workspace/src \
  MPLBACKEND=Agg \
  TZ=Europe/Helsinki \
  PYTHONBREAKPOINT=IPython.core.debugger.set_trace

# graphviz: PRISMA flow diagrams; build-essential: fallback for any wheel-less deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    curl \
    git \
    graphviz \
    less \
    nano \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Exact versions from the lock file, so a rebuild gives the same environment (see README)
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.lock.txt

# Shell and IPython settings are read from the mounted project (docker/), so edits there
# apply in the next shell or IPython session without a rebuild
RUN mkdir -p /root/.ipython/profile_default/startup \
  && ln -s /workspace/docker/ipython_config.py /root/.ipython/profile_default/ipython_config.py \
  && ln -s /workspace/docker/00-litmap.py /root/.ipython/profile_default/startup/00-litmap.py \
  && echo "source /workspace/docker/bashrc" > /root/.bashrc

SHELL ["/bin/bash", "-c"]

# Keep the container alive so you can open shells into it from Docker Desktop (Exec tab)
CMD ["sleep", "infinity"]
