import os
from elasticsearch import Elasticsearch


def get_es_client() -> Elasticsearch:
    url = os.getenv("ES_URL", "http://8.212.158.149:9200")
    return Elasticsearch(url)


def get_index_prefix() -> str:
    return os.getenv("ES_INDEX_PREFIX", "q6")
