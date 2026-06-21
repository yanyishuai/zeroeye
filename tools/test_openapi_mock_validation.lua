local function run(command)
  local ok, reason, code = os.execute(command)
  if ok == true then
    return 0
  end
  return code or 1
end

local function assert_exit(name, command, expected)
  local code = run(command)
  local matches = expected == "nonzero" and code ~= 0 or code == expected
  if not matches then
    io.stderr:write(name .. " expected exit " .. expected .. " but got " .. code .. "\n")
    os.exit(1)
  end
  print("ok - " .. name)
end

local base = "lua tools/openapi_mock.lua --validate-mocks"

assert_exit(
  "valid fixture passes mock validation",
  "OPENAPI_SPEC_PATH=docs/openapi/fixtures/mock-valid.yaml " .. base,
  0
)

assert_exit(
  "invalid fixture fails startup validation",
  "OPENAPI_SPEC_PATH=docs/openapi/fixtures/mock-invalid.yaml " .. base .. " >/tmp/openapi_mock_invalid.out 2>&1",
  "nonzero"
)

assert_exit(
  "allow-invalid-mocks bypasses fixture failure",
  "OPENAPI_SPEC_PATH=docs/openapi/fixtures/mock-invalid.yaml " .. base .. " --allow-invalid-mocks",
  0
)
