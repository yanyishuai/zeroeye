local function run(command)
  local handle = assert(io.popen(command .. " 2>&1"))
  local output = handle:read("*all")
  local ok, _, code = handle:close()
  if not ok then
    error("command failed (" .. tostring(code) .. "): " .. command .. "\n" .. output)
  end
  return output
end

local function assert_contains(output, needle)
  if not output:find(needle, 1, true) then
    error("expected output to contain: " .. needle .. "\nactual output:\n" .. output)
  end
end

local fixture_dir = "tools/testdata/openapi-diff"

local ordering = run(
  "lua tools/openapi_diff.lua --left " .. fixture_dir .. "/order-left.yaml" ..
  " --right " .. fixture_dir .. "/order-right.yaml --json"
)
assert_contains(ordering, [["changed":0]])
assert_contains(ordering, [["changed_schemas":0]])
assert_contains(ordering, [["added":0]])
assert_contains(ordering, [["removed":0]])

local ordering_again = run(
  "lua tools/openapi_diff.lua --right " .. fixture_dir .. "/order-right.yaml" ..
  " --left " .. fixture_dir .. "/order-left.yaml --json"
)
if ordering ~= ordering_again then
  error("ordering-only JSON output should be deterministic regardless of input order\nfirst:\n" ..
    ordering .. "\nsecond:\n" .. ordering_again)
end

local real_change = run(
  "lua tools/openapi_diff.lua --left " .. fixture_dir .. "/order-left.yaml" ..
  " --right " .. fixture_dir .. "/change-right.yaml --json"
)
assert_contains(real_change, [["changed":1]])
assert_contains(real_change, [["changed_schemas":1]])
assert_contains(real_change, [["changed_schemas":["User"]])

print("openapi_diff normalization tests passed")
