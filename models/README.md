# Model files

StemForge expects the BS-RoFormer-SW model files to exist in the model directory at runtime:

```text
BS-Roformer-SW.ckpt
BS-Roformer-SW.yaml
```

The default model directory is:

```text
/models/bs_roformer_sw
```

You can override it with:

```text
STEMFORGE_MODEL_DIR=/runpod-volume/models/bs_roformer_sw
```

Do not commit large model weights to this repository. For the first RunPod Serverless production test, mount a RunPod network volume containing these files and set `STEMFORGE_MODEL_DIR` to that mounted path.

Later, if cold starts are too slow or volume availability becomes awkward, bake the model files into the Docker image during the image build.
