# LiteLABS Research Worker

This branch is for experimental LiteLABS model testing only.

It is intentionally separate from the live XenForo-connected worker flow. Use this branch/image for experiments such as:

- backing vocal split testing
- lead vocal / karaoke model experiments
- Banquet / Query-Bandit experiments
- alternative bass extraction tests
- silent or near-silent stem validation

## Deployment idea

Create a second RunPod Serverless endpoint and point it at the research image, not the live LiteLABS image.

Suggested endpoint settings:

```text
Endpoint name: LiteLABS Research
Type: Queue based
Active workers: 0
Max workers: 1
FlashBoot: enabled
Network volume: attach only if the test model needs it
```

Use this endpoint for manual RunPod test payloads only. Do not connect it to XenForo users.

## Current branch status

This branch currently starts from the live LiteLABS worker codebase so it has the same working RunPod foundation. Experimental modes can now be added here without touching `main`.
