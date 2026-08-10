from app_server.redis_client import get_redis_client


def test_get_redis_client_returns_the_same_instance_across_calls():
    # The whole point of lru_cache-ing this is one pooled connection shared
    # by every caller, not a fresh Redis.from_url() (and fresh TCP
    # handshake) each time - proven by identity, not just equal config.
    assert get_redis_client() is get_redis_client()


def test_get_redis_client_is_a_working_connection():
    client = get_redis_client()
    assert client.ping() is True
