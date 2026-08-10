-- Customer control over the one part of a managed audit that is not
-- evidence-backed.
--
-- Aletheore's core promise is "no claim without evidence". The LLM suggestion
-- section (scan_worker/managed_audit.py) is the single deliberate exception:
-- a model's own rating and improvement ideas, clearly labeled, appended after
-- the cited findings. It has always been well separated visually, but there
-- was no way to decline it - a customer who bought the evidence-first promise
-- had that section in every signed report whether they wanted it or not.
--
-- Defaults to true, preserving existing behavior: this adds a switch, it does
-- not silently withdraw a feature paying installations already receive.
ALTER TABLE installations
    ADD COLUMN IF NOT EXISTS llm_suggestions_enabled BOOLEAN NOT NULL DEFAULT true;
