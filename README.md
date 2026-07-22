# Build Semantic Image Search Without Managing GPUs

**NobleLogic CLIP API** — production-ready multimodal embeddings in minutes.

Generate image and text embeddings with a simple HTTP request. No CUDA setup. No model downloads. No infrastructure to maintain.

[Get API Key](https://noblelogicllc.com/#pricing) · [Public Reference & Examples](https://github.com/NobleLogic1/clip-api-public) · [Documentation](https://noblelogicllc.com/#api-docs)

---

## Why this exists

OpenAI released CLIP in 2021 and stopped maintaining the production path. Running it yourself means:

- GPU hardware or expensive cloud instances
- Broken dependencies and version conflicts
- Weeks of setup and ongoing maintenance

Most teams give up. This API fixes that.

## Quick Start (under 2 minutes)

```bash
curl -X POST https://web-production-58f81.up.railway.app/embed/text \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "aerial photograph of a flooded field"}'
```

Response:

```json
{
  "embedding": [0.031, -0.184, 0.092, ...],
  "dimensions": 512
}
```

### Image embedding

```bash
curl -X POST https://web-production-58f81.up.railway.app/embed/image \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"image_url": "https://example.com/photo.jpg"}'
```

### Similarity scoring

```bash
curl -X POST https://web-production-58f81.up.railway.app/similarity \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"embedding_a": [...], "embedding_b": [...]}'
```

---

## Self-Hosting vs Managed API

| | Self-Hosting CLIP | NobleLogic CLIP API |
|---|---|---|
| **Time to first result** | Days to weeks | < 2 minutes |
| **GPU required** | Yes | No |
| **Dependency hell** | Frequent | None |
| **Monthly infra cost** | $80–$400+ | Starts at $29 |
| **Uptime & monitoring** | Your problem | Included |
| **Model updates** | Manual | Handled for you |
| **Scaling** | You manage | Automatic |

---

## What you can build

- **Semantic image search** — search photo libraries with natural language
- **Product matching** — find visually similar products for e-commerce
- **Image deduplication** — detect near-duplicates at scale
- **Content moderation** — flag images matching harmful descriptions
- **Multimodal RAG** — feed image + text embeddings into vector databases (Pinecone, Weaviate, pgvector)
- **Drone / aerial analysis** — describe terrain in plain English and retrieve matching imagery

---

## Pricing

| Plan | Price | API Calls / month |
|------|-------|-------------------|
| Developer | $29 | 500,000 |
| Professional | $99 | 2,500,000 |
| Enterprise | $299 | 10,000,000 |

[Start free trial / Get API Key →](https://noblelogicllc.com/#pricing)

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/embed/text` | Generate 512-dim embedding from text |
| `POST` | `/embed/image` | Generate 512-dim embedding from image URL |
| `POST` | `/similarity` | Cosine similarity between two embeddings |
| `GET` | `/health` | Service health + model status |

All endpoints require `Authorization: Bearer YOUR_API_KEY`.

---

## Public Reference Repository

Looking for examples, API reference, or a clean reference implementation?

→ **[clip-api-public](https://github.com/NobleLogic1/clip-api-public)**

---

## Live Demo

> Interactive demo coming soon on Hugging Face Spaces.
>
> In the meantime, get an API key and try the endpoints above in under 2 minutes.

---

## Support

- Landing page & docs: [noblelogicllc.com](https://noblelogicllc.com)
- Public examples: [github.com/NobleLogic1/clip-api-public](https://github.com/NobleLogic1/clip-api-public)
- Email: mark@noblelogicllc.com

---

**Made by NobleLogic LLC** — Fort Pierce, FL
