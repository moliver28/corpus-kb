# RAG glossary

One line per term. These definitions are sample content for the
retrieval demo, and they are accurate.

- **Chunk**: one coherent piece of a document, and the unit of search.
- **Embedding**: a list of numbers that represents the meaning of a text.
- **Vector search**: finding chunks whose embeddings sit close to the
  question embedding.
- **Full-text search**: keyword matching over a Postgres text index.
- **Reciprocal rank fusion**: merging two ranked lists by adding inverse
  rank scores.
- **Reranker**: a model that reorders fused candidates by relevance.
- **Golden set**: labeled questions with the chunk ids a good answer
  must find.
- **Recall@k**: how often the right chunk appears in the top k results.
- **MRR**: mean reciprocal rank; how high the first good hit lands.
- **Degraded chunk**: a chunk whose vector is a placeholder, written
  when the embedding model was unreachable.
