from .models import KnowledgeChunk


def create_chunk(title: str, content: str) -> KnowledgeChunk:
    return KnowledgeChunk.objects.create(title=title, content=content)
