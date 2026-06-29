# frozen_string_literal: true

# Pure reconnect backoff helper for MarketStream.
# Extracted for unit testing without EventMachine or network I/O.

module ReconnectBackoff
  module_function

  def compute_delay(
    attempt,
    base: 1,
    max_delay: 120,
    jitter_fraction: 0.0,
    rng: Random.new(0)
  )
    raise ArgumentError, 'attempt must be >= 0' if attempt.negative?

    exponential = base * (2**attempt)
    capped = [exponential, max_delay].min
    return capped if jitter_fraction <= 0

    spread = capped * jitter_fraction
    capped + (rng.rand * 2 * spread - spread)
  end

  def delay_bounds(attempt, base: 1, max_delay: 120, jitter_fraction: 0.0)
    center = compute_delay(attempt, base: base, max_delay: max_delay, jitter_fraction: 0.0)
    spread = center * jitter_fraction
    [center - spread, center + spread]
  end
end
