CREATE TABLE IF NOT EXISTS flash_review_monthly_count (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    month            DATE NOT NULL,
    review_count     INT NOT NULL DEFAULT 0,
    PRIMARY KEY (installation_id, month)
);
