"""A minimal OpenAI-compatible /v1/embeddings server, so OpenViking can start.

OpenViking's default embedder downloads a GGUF from huggingface.co, which this
environment's proxy refuses (403 at CONNECT). Its config explicitly supports pointing at
"local OpenAI-compatible servers", so this is that. Vectors are a deterministic hash of
the text: useless for semantics, sufficient for exercising a real server.
"""
import hashlib, json, math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 256

def vector(text: str):
    out = []
    seed = text.encode()
    while len(out) < DIM:
        seed = hashlib.blake2b(seed, digest_size=64).digest()
        out.extend(b / 255.0 - 0.5 for b in seed)
    v = out[:DIM]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        texts = body.get("input") or [""]
        if isinstance(texts, str):
            texts = [texts]
        payload = {
            "object": "list",
            "model": body.get("model", "stub"),
            "data": [{"object": "embedding", "index": i, "embedding": vector(str(t))}
                     for i, t in enumerate(texts)],
            "usage": {"prompt_tokens": sum(len(str(t)) for t in texts), "total_tokens": 0},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.send_response(200); self.send_header("content-length", "2"); self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18888), H).serve_forever()
