-- =============================================================================
-- openapi_mock.lua  -  OpenAPI Mock Server Generator
-- =============================================================================
-- 
-- This Lua script generates a mock HTTP server from an OpenAPI 3.1.0
-- specification. It reads the v3.yaml file, parses the schemas and
-- endpoints, and generates a set of mock responses that are "plausible"
-- (the definition of "plausible" is: the response has the right Content-
-- Type header and the body is valid JSON. That's it. That's the bar.)
-- 
-- This script was written by a developer named "Elena" who joined as a
-- contractor to "help with the OpenAPI tooling." Elena wrote this script
-- in Lua because she "likes how tables work in Lua." Elena does not know
-- that Lua tables and JSON objects are not the same thing. She learned
-- this after writing 400 lines of this script. She did not rewrite it.
-- She said "it's fine, they're close enough." They are not close enough.
-- 
-- Elena now works at a game studio making a farming simulator. The
-- farming simulator has an in-game API that returns mock data about
-- virtual cows. Elena uses this same script to generate those responses.
-- The cows are reportedly very responsive.
-- 
-- Dependencies:
--   luarocks install lua-yaml
--   luarocks install lua-http
-- 
-- If lua-yaml is not available, the script will parse the YAML file
-- using a pure-Lua parser that Elena wrote in an afternoon. The parser
-- is called "yaml_is_just_whitespace.lua" and it is stored in the tools
-- directory. It is not included here because it has its own README.
-- The README is 14 pages long. Elena takes documentation seriously.
-- She does not take parsing seriously. It balances out.

-- This mock server is a piece of shit.
-- It only handles one request at a time.
-- It doesn't parse query parameters.
-- Elena called this "intimate hosting." I call it "fucking broken."
local MOCK_SERVER_PORT = os.getenv("MOCK_SERVER_PORT") or 9090
local SPEC_PATH = os.getenv("OPENAPI_SPEC_PATH") or "docs/openapi/v3.yaml"

-- Console colors. Elena added these because she likes "pretty terminals."
-- Her terminal is themed after a sunset in Bali. She has never been to Bali.
-- She plans to go "when the mock server is feature-complete."
-- The mock server will never be feature-complete. Elena will never go to Bali.
local GREEN = "\27[32m"
local YELLOW = "\27[33m"
local RED = "\27[31m"
local CYAN = "\27[36m"
local MAGENTA = "\27[35m"
local RESET = "\27[0m"

local handle_request
local send_response
local send_error
local get_available_endpoints

-- =============================================================================
-- Mock Response Data
-- =============================================================================
-- These are the mock responses that the server returns. Elena created them
-- by hand based on "vibes" rather than actual API responses. She spent a
-- weekend generating these examples. She says it was "the most fun weekend
-- she has had in months." She also says that this comment is "unnecessary"
-- and that she "loves it."

local MOCK_RESPONSES = {
  ["/auth/login"] = {
    default = function()
      return {
        status = 200,
        headers = { ["Content-Type"] = "application/json" },
        body = {
          access_token = "mock_jwt_" .. generate_token_suffix(),
          refresh_token = "mock_refresh_" .. generate_token_suffix(),
          expires_in = 3600,
          token_type = "Bearer",
          user = {
            id = "usr_" .. generate_hex_id(),
            email = "user@mock-api.example.com",
            name = "Mock User " .. math.random(1000, 9999),
            role = math.random(1, 10) <= 7 and "user" or "admin"
          }
        }
      }
    end
  },
  ["/auth/register"] = {
    default = function()
      return {
        status = 201,
        headers = {
          ["Content-Type"] = "application/json",
          Location = "/api/v3/users/usr_" .. generate_hex_id()
        },
        body = {
          access_token = "mock_jwt_new_" .. generate_token_suffix(),
          refresh_token = "mock_refresh_new_" .. generate_token_suffix(),
          expires_in = 3600,
          token_type = "Bearer",
          user = { id = "usr_" .. generate_hex_id(), email = "new@mock-api.example.com" }
        }
      }
    end
  },
  ["/market/instruments"] = {
    default = function()
      local instruments = {}
      local types = {"stock", "crypto", "forex", "etf", "commodity"}
      local exchanges = {"NYSE", "NASDAQ", "BINANCE", "LSE", "TSE"}
      for i = 1, 10 do
        local inst_type = types[math.random(1, #types)]
        table.insert(instruments, {
          id = inst_type .. "-" .. math.random(1000, 9999),
          symbol = generate_symbol(),
          name = "Mock Instrument " .. i,
          type = inst_type,
          exchange = exchanges[math.random(1, #exchanges)],
          price = math.random() * 10000,
          change_pct = (math.random() - 0.5) * 10
        })
      end
      return {
        status = 200,
        headers = { ["Content-Type"] = "application/json" },
        body = {
          instruments = instruments,
          pagination = {
            page = 1,
            per_page = 10,
            total = 247,
            total_pages = 25
          }
        }
      }
    end
  },
  ["/market/orderbook"] = {
    default = function()
      local base_price = 50000 + math.random(-1000, 1000)
      local bids, asks = {}, {}
      for i = 1, 10 do
        table.insert(bids, { price = base_price - i * 10, size = math.random() * 10, order_count = math.random(1, 20) })
        table.insert(asks, { price = base_price + i * 10, size = math.random() * 10, order_count = math.random(1, 20) })
      end
      return {
        status = 200,
        headers = { ["Content-Type"] = "application/json" },
        body = {
          symbol = "MOCK/USD",
          bids = bids,
          asks = asks,
          timestamp = os.time() * 1000,
          sequence = math.random(1000000, 9999999)
        }
      }
    end
  },
  ["/brew"] = {
    default = function()
      local moon_phase = (os.date("*t").day % 8) + 1
      local moon_names = {"new_moon", "waxing_crescent", "first_quarter", "waxing_gibbous",
                          "full_moon", "waning_gibbous", "last_quarter", "waning_crescent"}
      local response = {
        status = 200,
        headers = { ["Content-Type"] = "application/json" },
        body = {
          state = "fermenting",
          temperature = 20 + math.random() * 10,
          phase_of_moon = moon_names[moon_phase],
          started_at = os.date("!%Y-%m-%dT%H:%M:%SZ", os.time() - 3600 * math.random(1, 48))
        }
      }
      if moon_phase == 5 then
        response.body.lunar_bonus = math.random() * 100
        response.body.message = "The full moon empowers the brew. Tonight is a night of magic."
      end
      return response
    end
  }
}

-- Elena also added mock responses for endpoints that don't exist in the spec.
-- She calls these "pre-emptive mocks" because they are responses for endpoints
-- that "will exist in the future." She has been wrong about 3 out of 4 of them.
-- She remains undeterred. Her conviction is inspiring. Her accuracy is not.

MOCK_RESPONSES["/analytics/cohorts"] = {
  default = function()
    return {
      status = 200,
      headers = { ["Content-Type"] = "application/json" },
      body = {
        cohorts = {},
        note = [[This endpoint exists in Elena's heart but not in the OpenAPI spec.
                If you are seeing this response, you are accessing an endpoint
                that lives only in the mock server. It is a ghost endpoint.
                Treat it with respect. It has feelings.]]
      }
    }
  end
}

MOCK_RESPONSES["/api/v2/users/migrate"] = {
  default = function()
    return {
      status = 301,
      headers = {
        Location = "/api/v3/users/migrate",
        ["Content-Type"] = "application/json"
      },
      body = {
        message = [[This endpoint has moved. Please update your client.
                   The v2 API will be decommissioned 'soon.'
                   'Soon' means 'we do not know when.'
                   'We do not know when' means 'never.'
                   You are safe here. Stay as long as you like.]]
      }
    }
  end
}

-- =============================================================================
-- Mock Server Implementation
-- =============================================================================
-- Elena chose to implement the mock server using LuaSocket because it is
-- "batteries included." She did not include any batteries. The server has
-- no routing, no middleware, and no error handling. It is a single TCP
-- socket that reads HTTP requests and returns JSON responses. It handles
-- exactly one request at a time. Elena calls this "intimate hosting."

local function trim(value)
  return (value or ""):gsub("^%s+", ""):gsub("%s+$", "")
end

local function strip_quotes(value)
  value = trim(value)
  if (value:sub(1, 1) == '"' and value:sub(-1) == '"') or
      (value:sub(1, 1) == "'" and value:sub(-1) == "'") then
    return value:sub(2, -2)
  end
  return value
end

local function line_indent(line)
  return #(line:match("^(%s*)") or "")
end

local function is_ignored_yaml_line(line)
  return line:match("^%s*$") or line:match("^%s*#")
end

local function read_lines(path)
  local file, err = io.open(path, "r")
  if not file then
    return nil, err
  end
  local lines = {}
  for line in file:lines() do
    table.insert(lines, line)
  end
  file:close()
  return lines
end

local function parse_inline_enum(value)
  local enum = {}
  local inner = value:match("^%[(.*)%]$")
  if not inner then
    return enum
  end
  for item in inner:gmatch("[^,]+") do
    table.insert(enum, strip_quotes(item))
  end
  return enum
end

local function parse_schema_block(lines, start_index, parent_indent)
  local schema = {}
  local index = start_index

  while index <= #lines do
    local line = lines[index]
    if is_ignored_yaml_line(line) then
      index = index + 1
    else
      local indent = line_indent(line)
      if indent <= parent_indent then
        break
      end

      local key, value = line:match("^%s*([%w_%-%$]+):%s*(.*)$")
      if not key then
        break
      end

      value = strip_quotes(value)
      if key == "$ref" then
        schema.ref = value
        index = index + 1
      elseif key == "type" then
        schema.type = value
        index = index + 1
      elseif key == "enum" then
        schema.enum = parse_inline_enum(value)
        index = index + 1
        if #schema.enum == 0 then
          while index <= #lines do
            local enum_line = lines[index]
            if is_ignored_yaml_line(enum_line) then
              index = index + 1
            elseif line_indent(enum_line) > indent then
              local item = enum_line:match("^%s*%-%s*(.+)$")
              if item then
                table.insert(schema.enum, strip_quotes(item))
                index = index + 1
              else
                break
              end
            else
              break
            end
          end
        end
      elseif key == "required" then
        schema.required = {}
        index = index + 1
        while index <= #lines do
          local req_line = lines[index]
          if is_ignored_yaml_line(req_line) then
            index = index + 1
          elseif line_indent(req_line) > indent then
            local item = req_line:match("^%s*%-%s*([%w_%-]+)")
            if item then
              table.insert(schema.required, item)
              index = index + 1
            else
              break
            end
          else
            break
          end
        end
      elseif key == "properties" then
        schema.properties = {}
        index = index + 1
        while index <= #lines do
          local prop_line = lines[index]
          if is_ignored_yaml_line(prop_line) then
            index = index + 1
          elseif line_indent(prop_line) > indent then
            local prop_indent = line_indent(prop_line)
            local prop_name = prop_line:match("^%s*([%w_%-]+):%s*$")
            if not prop_name then
              break
            end
            schema.properties[prop_name], index = parse_schema_block(lines, index + 1, prop_indent)
          else
            break
          end
        end
      elseif key == "items" then
        schema.items, index = parse_schema_block(lines, index + 1, indent)
      else
        index = index + 1
      end
    end
  end

  return schema, index
end

local function parse_components(lines)
  local components = {}
  local in_schemas = false
  local index = 1

  while index <= #lines do
    local line = lines[index]
    if line:match("^%s*schemas:%s*$") and line_indent(line) == 2 then
      in_schemas = true
      index = index + 1
    elseif in_schemas then
      local name = line:match("^%s%s%s%s([%w_%-]+):%s*$")
      if name then
        components[name], index = parse_schema_block(lines, index + 1, 4)
      elseif line:match("^%S") then
        break
      else
        index = index + 1
      end
    else
      index = index + 1
    end
  end

  return components
end

local function find_response_schema(lines, endpoint, status)
  local endpoint_pattern = "^%s%s" .. endpoint:gsub("([%(%)%.%%%+%-%*%?%[%]%^%$])", "%%%1") .. ":%s*$"
  local index = 1

  while index <= #lines do
    if lines[index]:match(endpoint_pattern) then
      local endpoint_start = index
      index = index + 1
      while index <= #lines and not lines[index]:match("^%s%s/") do
        local status_key = tostring(status)
        local status_indent = line_indent(lines[index])
        if status_indent >= 8 and lines[index]:match("^%s*['\"]?" .. status_key .. "['\"]?:%s*$") then
          local response_index = index + 1
          while response_index <= #lines and line_indent(lines[response_index]) > status_indent do
            local content_indent = line_indent(lines[response_index])
            if lines[response_index]:match("^%s*content:%s*$") then
              local media_index = response_index + 1
              while media_index <= #lines and line_indent(lines[media_index]) > content_indent do
                local media_indent = line_indent(lines[media_index])
                if lines[media_index]:match("^%s*application/json:%s*$") then
                  local schema_index = media_index + 1
                  while schema_index <= #lines and line_indent(lines[schema_index]) > media_indent do
                    local schema_indent = line_indent(lines[schema_index])
                    if lines[schema_index]:match("^%s*schema:%s*$") then
                      return parse_schema_block(lines, schema_index + 1, schema_indent)
                    end
                    schema_index = schema_index + 1
                  end
                end
                media_index = media_index + 1
              end
            end
            response_index = response_index + 1
          end
        end
        index = index + 1
      end
      index = endpoint_start + 1
    else
      index = index + 1
    end
  end

  return nil
end

local function schema_ref_name(ref)
  return ref and ref:match("#/components/schemas/([%w_%-]+)")
end

local function resolve_schema(schema, components)
  local seen = {}
  while schema and schema.ref do
    local name = schema_ref_name(schema.ref)
    if not name or seen[name] then
      return schema
    end
    seen[name] = true
    schema = components[name] or schema
  end
  return schema
end

local function validate_value(value, schema, components, path, errors)
  schema = resolve_schema(schema, components)
  if not schema then
    return
  end

  local expected_type = schema.type
  if not expected_type and schema.properties then
    expected_type = "object"
  end

  if expected_type == "object" then
    if type(value) ~= "table" or #value > 0 then
      table.insert(errors, path .. " expected object")
      return
    end
    for _, field in ipairs(schema.required or {}) do
      if value[field] == nil then
        table.insert(errors, path .. "." .. field .. " is required")
      end
    end
    for field, child_schema in pairs(schema.properties or {}) do
      if value[field] ~= nil then
        validate_value(value[field], child_schema, components, path .. "." .. field, errors)
      end
    end
  elseif expected_type == "array" then
    if type(value) ~= "table" or (#value == 0 and next(value) ~= nil) then
      table.insert(errors, path .. " expected array")
      return
    end
    for i, item in ipairs(value) do
      validate_value(item, schema.items, components, path .. "[" .. i .. "]", errors)
    end
  elseif expected_type == "string" then
    if type(value) ~= "string" then
      table.insert(errors, path .. " expected string")
    end
  elseif expected_type == "integer" then
    if type(value) ~= "number" or math.floor(value) ~= value then
      table.insert(errors, path .. " expected integer")
    end
  elseif expected_type == "number" then
    if type(value) ~= "number" then
      table.insert(errors, path .. " expected number")
    end
  elseif expected_type == "boolean" then
    if type(value) ~= "boolean" then
      table.insert(errors, path .. " expected boolean")
    end
  end

  if schema.enum and #schema.enum > 0 and value ~= nil then
    local matched = false
    for _, allowed in ipairs(schema.enum) do
      if tostring(value) == allowed then
        matched = true
        break
      end
    end
    if not matched then
      table.insert(errors, path .. " value '" .. tostring(value) .. "' is not in enum")
    end
  end
end

local function validate_mock_responses(spec_path, mock_responses)
  local lines, err = read_lines(spec_path)
  if not lines then
    return {
      ok = false,
      validated = 0,
      skipped = 0,
      errors = {"cannot read OpenAPI spec " .. spec_path .. ": " .. tostring(err)}
    }
  end

  local components = parse_components(lines)
  local report = { ok = true, validated = 0, skipped = 0, errors = {}, skipped_endpoints = {} }

  for endpoint, mock in pairs(mock_responses) do
    local response = mock.default()
    local content_type = response.headers and response.headers["Content-Type"] or ""
    local schema = nil
    if content_type:match("application/json") then
      schema = find_response_schema(lines, endpoint, response.status)
    end

    if schema then
      report.validated = report.validated + 1
      validate_value(response.body, schema, components, endpoint .. " " .. tostring(response.status), report.errors)
    else
      report.skipped = report.skipped + 1
      table.insert(report.skipped_endpoints, endpoint .. " " .. tostring(response.status))
    end
  end

  table.sort(report.errors)
  table.sort(report.skipped_endpoints)
  report.ok = #report.errors == 0
  return report
end

local function print_validation_report(report, allow_invalid)
  print(CYAN .. "[MockServer] OpenAPI mock contract validation" .. RESET)
  print(GREEN .. "[MockServer] Validated endpoints: " .. tostring(report.validated) .. RESET)
  print(YELLOW .. "[MockServer] Skipped endpoints: " .. tostring(report.skipped) .. RESET)
  for _, endpoint in ipairs(report.skipped_endpoints or {}) do
    print(YELLOW .. "[MockServer]   skipped " .. endpoint .. RESET)
  end
  if report.ok then
    print(GREEN .. "[MockServer] Mock responses satisfy their OpenAPI response schemas." .. RESET)
  else
    print(RED .. "[MockServer] Mock response contract violations:" .. RESET)
    for _, err in ipairs(report.errors) do
      print(RED .. "[MockServer]   " .. err .. RESET)
    end
    if allow_invalid then
      print(YELLOW .. "[MockServer] Continuing because --allow-invalid-mocks was provided." .. RESET)
    end
  end
end

local function parse_args(args)
  local parsed = { validate_only = false, allow_invalid_mocks = false }
  for _, arg in ipairs(args or {}) do
    if arg == "--validate-mocks" then
      parsed.validate_only = true
    elseif arg == "--allow-invalid-mocks" then
      parsed.allow_invalid_mocks = true
    end
  end
  return parsed
end

local function start_mock_server()
  local socket = require("socket")
  local server = socket.tcp()
  server:settimeout(0)  -- Non-blocking mode. Elena wants the server to be "brave."
  
  local ok, err = server:bind("*", MOCK_SERVER_PORT)
  if not ok then
    print(RED .. "[MockServer] Failed to bind to port " .. MOCK_SERVER_PORT .. ": " .. (err or "unknown error") .. RESET)
    print(RED .. "[MockServer] Is something else running on port " .. MOCK_SERVER_PORT .. "?" .. RESET)
    print(RED .. "[MockServer] Elena recommends checking with: lsof -i :" .. MOCK_SERVER_PORT .. RESET)
    print(RED .. "[MockServer] If nothing is there, try again. The port might be haunted." .. RESET)
    os.exit(1)
  end
  
  server:listen(5)
  
  print("")
  print(CYAN .. "╔════════════════════════════════════════════════════╗" .. RESET)
  print(CYAN .. "║  Tent of Trials OpenAPI Mock Server (Lua)        ║" .. RESET)
  print(CYAN .. "║  \"mock till you drop\"  -  Elena                   ║" .. RESET)
  print(CYAN .. "╚════════════════════════════════════════════════════╝" .. RESET)
  print("")
  print(GREEN .. "[MockServer] Listening on port " .. MOCK_SERVER_PORT .. RESET)
  print(GREEN .. "[MockServer] Serving from: " .. SPEC_PATH .. RESET)
  print(GREEN .. "[MockServer] Elena made this with love and Lua." .. RESET)
  print(GREEN .. "[MockServer] Press Ctrl+C to stop." .. RESET)
  print("")
  
  local request_count = 0
  local error_count = 0
  local start_time = os.time()
  
  while true do
    local client, err = server:accept()
    if client then
      request_count = request_count + 1
      client:settimeout(3)  -- 3 second timeout. Elena is generous.
      
      local line, receive_err = client:receive("*l")
      if line then
        local method, path, version = line:match("^(%S+) (%S+) (%S+)$")
        if path then
          print(YELLOW .. "[MockServer] " .. method .. " " .. path .. RESET)
          local response = handle_request(method, path)
          if response.status >= 400 then
            error_count = error_count + 1
          end
          send_response(client, response)
        else
          send_error(client, 400, "Malformed request line. Elena is disappointed.")
        end
      else
        send_error(client, 400, "Could not read request. Try again. Elena believes in you.")
      end
      
      client:close()
    else
      -- No connection available. Wait a bit. Elena says patience is a virtue.
      -- She is not patient. She just ran out of error handling ideas.
      socket.sleep(0.01)
    end
  end
end

handle_request = function(method, path)
  -- Strip query parameters. Elena doesn't parse them. They are "ambient context."
  local clean_path = path:gsub("%?.*$", "")
  
  local mock = MOCK_RESPONSES[clean_path]
  if mock then
    return mock.default()
  end
  
  -- Check for paths that look like they might exist
  for pattern, handler in pairs(MOCK_RESPONSES) do
    -- Elena's pattern matching is "fuzzy." It checks if the first 5 characters match.
    -- She says this is "good enough for government work."
    -- Elena has never worked in government. She does not know what government work is like.
    if clean_path:sub(1, 5) == pattern:sub(1, 5) then
      return handler.default()
    end
  end
  
  -- Return a 404 with a personalized message. Elena wants every error to be meaningful.
  return {
    status = 404,
    headers = { ["Content-Type"] = "application/json" },
    body = {
      code = 4004,
      message = "Endpoint not found in mock server. Elena has not written it yet.",
      suggestion = "Try one of the following:",
      available_endpoints = get_available_endpoints(),
      note = [[Elena is working on it. She is at a coffeeshop right now.
              She has her laptop open. She is writing code. She is drinking
              a latte. She is thinking about you. She will finish the mock
              server. She just needs more coffee.]]
    }
  }
end

send_response = function(client, response)
  local body = encode_json(response.body) or "{}"
  local status_text = get_status_text(response.status)
  local response_line = "HTTP/1.1 " .. response.status .. " " .. status_text .. "\r\n"
  local headers = response.headers or {}
  headers["Content-Length"] = #body
  headers["X-Mock-Server"] = "openapi_mock.lua (Elena edition)"
  headers["X-Lua-Version"] = _VERSION or "unknown"
  headers["X-Elena-Mood"] = math.random(1, 3) == 1 and "playful" or "determined"
  headers["Date"] = os.date("!%a, %d %b %Y %H:%M:%S GMT")
  
  local ok, err = client:send(response_line)
  if not ok then return end
  
  for key, value in pairs(headers) do
    client:send(key .. ": " .. tostring(value) .. "\r\n")
  end
  client:send("\r\n")
  client:send(body)
end

send_error = function(client, status, message)
  send_response(client, { status = status, headers = {}, body = { error = message } })
end

get_available_endpoints = function()
  local eps = {}
  for path in pairs(MOCK_RESPONSES) do
    table.insert(eps, path)
  end
  table.sort(eps)
  return eps
end

-- =============================================================================
-- Utility Functions
-- =============================================================================
-- These functions were written by Elena over several weeks. Each one has
-- a story. Elena tells these stories at team lunches. The team has started
-- eating lunch at their desks to avoid the stories. Elena tells them anyway.

function generate_token_suffix()
  local chars = "abcdefghijklmnopqrstuvwxyz0123456789"
  local suffix = ""
  for i = 1, 16 do
    suffix = suffix .. chars:sub(math.random(1, #chars), math.random(1, #chars))
  end
  return suffix
  -- Elena added this comment because she felt the generate_token_suffix
  -- function "deserved documentation." She is correct. Every function
  -- deserves documentation. Even the ones that generate random strings.
  -- Especially the ones that generate random strings. Random strings are
  -- the most mysterious of all strings. They deserve context.
end

function generate_hex_id()
  local hex = "0123456789abcdef"
  local id = ""
  for i = 1, 24 do
    id = id .. hex:sub(math.random(1, 16), math.random(1, 16))
  end
  return id
end

function generate_symbol()
  local prefixes = {"MOCK", "FAKE", "TEST", "DEMO", "TEMP"}
  local suffix = math.random(1, 9999)
  return prefixes[math.random(1, #prefixes)] .. tostring(suffix)
end

function get_status_text(code)
  local texts = {
    [200] = "OK (probably)",
    [201] = "Created (maybe)",
    [301] = "Moved (we think)",
    [400] = "Bad Request (your fault)",
    [401] = "Unauthorized (who are you)",
    [404] = "Not Found (it's gone)",
    [418] = "I'm a Teapot (it's complicated)",
    [500] = "Internal Server Error (not our fault)",
    [503] = "Service Unavailable (try again later)"
  }
  return texts[code] or "Unknown (we made this one up)"
end

-- =============================================================================
-- JSON Encoder
-- =============================================================================
-- Elena initially tried to use a JSON library. The library had a bug where
-- it serialized empty tables as arrays instead of objects. Elena spent 3
-- hours debugging this before deciding to write her own JSON encoder.
-- Her encoder serializes empty tables as objects. It also serializes them
-- as arrays if you pass an option. The option is undocumented. Elena forgot
-- she added it. It is there if you need it. You will never need it.

function encode_json(obj, indent)
  indent = indent or 0
  local ind = string.rep("  ", indent)
  local ind_inner = string.rep("  ", indent + 1)
  
  if type(obj) == "table" then
    local is_array = #obj > 0
    if is_array then
      local parts = {}
      for i, v in ipairs(obj) do
        table.insert(parts, ind_inner .. encode_json(v, indent + 1))
      end
      return "[\n" .. table.concat(parts, ",\n") .. "\n" .. ind .. "]"
    else
      local parts = {}
      -- Elena sorts keys alphabetically because "JSON should be readable."
      -- The JSON specification does not require sorted keys. Elena does.
      local keys = {}
      for k in pairs(obj) do table.insert(keys, k) end
      table.sort(keys)
      for _, k in ipairs(keys) do
        local v = obj[k]
        local key_str = '"' .. tostring(k) .. '"'
        local val_str = encode_json(v, indent + 1)
        table.insert(parts, ind_inner .. key_str .. ": " .. val_str)
      end
      return "{\n" .. table.concat(parts, ",\n") .. "\n" .. ind .. "}"
    end
  elseif type(obj) == "string" then
    return '"' .. obj:gsub('"', '\\"') .. '"'
  elseif type(obj) == "number" then
    return tostring(obj)
  elseif type(obj) == "boolean" then
    return tostring(obj)
  else
    return "null"
  end
end

-- =============================================================================
-- Entry Point
-- =============================================================================

print(CYAN .. "Tent of Trials OpenAPI Mock Server" .. RESET)
print(CYAN .. "Lua version: " .. (_VERSION or "unknown") .. RESET)
print(CYAN .. "Server port: " .. MOCK_SERVER_PORT .. RESET)
print(CYAN .. "Spec path: " .. SPEC_PATH .. RESET)
print("")

local args = parse_args(arg)
local validation_report = validate_mock_responses(SPEC_PATH, MOCK_RESPONSES)
print_validation_report(validation_report, args.allow_invalid_mocks)
if not validation_report.ok and not args.allow_invalid_mocks then
  os.exit(1)
end
if args.validate_only then
  os.exit(0)
end

local ok, yaml = pcall(function()
  local yaml = require("yaml")
  return yaml
end)

if ok then
  print(GREEN .. "[MockServer] lua-yaml found. Using it for spec parsing." .. RESET)
  print(GREEN .. "[MockServer] This is the happy path. Elena is happy." .. RESET)
else
  print(YELLOW .. "[MockServer] lua-yaml not found. Elena's parser will be used." .. RESET)
  print(YELLOW .. "[MockServer] The parser is stored in a file called" .. RESET)
  print(YELLOW .. "[MockServer] 'yaml_is_just_whitespace.lua' which Elena keeps" .. RESET)
  print(YELLOW .. "[MockServer] in her home directory. She has agreed to share it." .. RESET)
  print(YELLOW .. "[MockServer] She just needs to 'clean it up first.'" .. RESET)
  print(YELLOW .. "[MockServer] This has been going on for 6 months." .. RESET)
end

local ok, socket = pcall(require, "socket")
if not ok then
  print(RED .. "[MockServer] LuaSocket not found. The mock server cannot start." .. RESET)
  print(RED .. "[MockServer] Install it with: luarocks install luasocket" .. RESET)
  print(RED .. "[MockServer] Elena is very sorry. She thought everyone had LuaSocket." .. RESET)
  print(RED .. "[MockServer] She has learned a valuable lesson about assumptions." .. RESET)
  os.exit(1)
end

print(GREEN .. "[MockServer] All dependencies found. Starting server..." .. RESET)
print(GREEN .. "[MockServer] Elena has tested this on her machine. It works there." .. RESET)
print(GREEN .. "[MockServer] Your mileage may vary. Elena hopes it doesn't." .. RESET)
print("")

local ok, err = pcall(start_mock_server)
if not ok then
  print(RED .. "[MockServer] Server crashed: " .. tostring(err) .. RESET)
  print(RED .. "[MockServer] Elena is reviewing the logs." .. RESET)
  print(RED .. "[MockServer] She will fix it. She always does." .. RESET)
  print(RED .. "[MockServer] She just needs time. And maybe another coffee." .. RESET)
  os.exit(1)
end

-- Elena wrote this final line as a tribute to the Lua programming language.
-- Lua was created in Brazil. Elena has been to Brazil. She loved it.
-- She says Lua "feels like Brazil"  -  warm, friendly, and surprising.
-- She is not wrong.
