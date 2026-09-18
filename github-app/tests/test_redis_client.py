from app_server.redis_client import (
    WEBHOOK_5XX_COUNT_KEY,
    WEBHOOK_5XX_WINDOW_SECONDS,
    get_redis_client,
    record_webhook_5xx,
)


def test_get_redis_client_returns_the_same_instance_across_calls():
    # The whole point of lru_cache-ing this is one pooled connection shared
    # by every caller, not a fresh Redis.from_url() (and fresh TCP
    # handshake) each time - proven by identity, not just equal config.
    assert get_redis_client() is get_redis_client()


def test_get_redis_client_is_a_working_connection():
    client = get_redis_client()
    assert client.ping() is True


def test_record_webhook_5xx_increments_a_durable_counter():
    client = get_redis_client()
    client.delete(WEBHOOK_5XX_COUNT_KEY)

    record_webhook_5xx(client)
    assert int(client.get(WEBHOOK_5XX_COUNT_KEY)) == 1

    record_webhook_5xx(client)
    assert int(client.get(WEBHOOK_5XX_COUNT_KEY)) == 2

    ttl = client.ttl(WEBHOOK_5XX_COUNT_KEY)
    assert 0 < ttl <= WEBHOOK_5XX_WINDOW_SECONDS

    client.delete(WEBHOOK_5XX_COUNT_KEY)
