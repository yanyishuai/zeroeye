#!/usr/bin/env ruby
# frozen_string_literal: true

require_relative 'reconnect_backoff'

def assert_eq(actual, expected, message)
  return if actual == expected

  warn "FAIL: #{message} (expected #{expected.inspect}, got #{actual.inspect})"
  exit 1
end

def assert_in_range(value, min, max, message)
  return if value >= min && value <= max

  warn "FAIL: #{message} (expected #{min}..#{max}, got #{value})"
  exit 1
end

# Initial delay uses attempt 0 -> base * 2**0
assert_eq(
  ReconnectBackoff.compute_delay(0, base: 1, max_delay: 120),
  1,
  'initial delay'
)

# Exponential growth
assert_eq(ReconnectBackoff.compute_delay(1, base: 1, max_delay: 120), 2, 'second attempt')
assert_eq(ReconnectBackoff.compute_delay(2, base: 1, max_delay: 120), 4, 'third attempt')

# Maximum cap
assert_eq(
  ReconnectBackoff.compute_delay(10, base: 1, max_delay: 120),
  120,
  'cap at max delay'
)

# Jitter stays within documented bounds
low, high = ReconnectBackoff.delay_bounds(3, base: 1, max_delay: 120, jitter_fraction: 0.1)
5.times do
  value = ReconnectBackoff.compute_delay(
    3,
    base: 1,
    max_delay: 120,
    jitter_fraction: 0.1,
    rng: Random.new(42)
  )
  assert_in_range(value, low, high, 'jitter within bounds')
end

# Default jitter disabled preserves legacy delays
assert_eq(
  ReconnectBackoff.compute_delay(4, base: 1, max_delay: 120, jitter_fraction: 0.0),
  16,
  'no jitter by default'
)

puts 'ReconnectBackoff tests passed'
