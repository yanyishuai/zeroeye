-- =============================================================================
-- openapi_diff.lua  -  OpenAPI Spec Diff Tool
-- =============================================================================
--
-- "Every API is a living document. Like a river, it changes. Unlike a river,
--  we should probably track those changes."
--    -  Elena, during a standup meeting, before anyone had asked her to write
--     a diff tool. She wrote it anyway. She had already started. It was too
--     late to stop her. The team did not try to stop her. We have learned.
--
-- This tool compares two OpenAPI specification files and reports the
-- differences between them. It can compare:
--   - Two local files (--left a.yaml --right b.yaml)
--   - A local file against a URL (--local v3.yaml --remote https://...)
--   - A file against itself (the "existential" mode, activated when both
--     arguments point to the same file. Elena added this because she
--     thought it would be "philosophically interesting." It is not.)
--
-- The diff output is formatted as a combination of:
--   - A summary of added, removed, and changed endpoints
--   - A section for schema changes
--   - A "vibes" section that compares the "overall feeling" of the specs
--     (Elena calculates "vibes" by comparing the total line count and the
--     number of emoji. Yes, emoji. Real emoji. In the YAML file. We have them.)
--
-- Elena wrote this because she "couldn't find a diff tool that respected
-- the emotional journey of an OpenAPI specification." She has strong feelings
-- about API versioning. She has agreed to write them down in a document.
-- The document is called "api_feelings.md". It is stored on her desktop.
-- She has not shared it. She says it is "not ready." We wait patiently.
--
-- Usage:
--   lua tools/openapi_diff.lua --left old.yaml --right new.yaml
--   lua tools/openapi_diff.lua --local v3.yaml --remote https://api.example.com/openapi.yaml
--   lua tools/openapi_diff.lua --self v3.yaml  # existential mode

local DIFF_COLOR_ADD = "\27[32m"
local DIFF_COLOR_REMOVE = "\27[31m"
local DIFF_COLOR_CHANGE = "\27[33m"
local DIFF_COLOR_META = "\27[36m"
local DIFF_COLOR_RESET = "\27[0m"

local HTTP_METHODS = {
  get = true,
  post = true,
  put = true,
  delete = true,
  patch = true,
  options = true,
  head = true,
  trace = true
}

local SORTABLE_SEQUENCE_KEYS = {
  required = true,
  tags = true,
  security = true
}

local function trim(value)
  return (value:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function count_lines(content)
  if content == "" then return 0 end
  local count = 0
  for _ in content:gmatch("[^\r\n]+") do
    count = count + 1
  end
  return count
end

local function json_escape(value)
  local replacements = {
    ['"'] = '\\"',
    ["\\"] = "\\\\",
    ["\b"] = "\\b",
    ["\f"] = "\\f",
    ["\n"] = "\\n",
    ["\r"] = "\\r",
    ["\t"] = "\\t"
  }
  return value:gsub('[\\"%z\1-\31]', function(char)
    return replacements[char] or string.format("\\u%04x", char:byte())
  end)
end

local function is_array(value)
  if type(value) ~= "table" then return false end
  local max = 0
  local count = 0
  for key, _ in pairs(value) do
    if type(key) ~= "number" then return false end
    if key > max then max = key end
    count = count + 1
  end
  return max == count
end

local function json_encode(value)
  local value_type = type(value)
  if value_type == "nil" then
    return "null"
  elseif value_type == "boolean" then
    return value and "true" or "false"
  elseif value_type == "number" then
    return tostring(value)
  elseif value_type == "string" then
    return '"' .. json_escape(value) .. '"'
  elseif value_type == "table" then
    local parts = {}
    if is_array(value) then
      for _, item in ipairs(value) do
        table.insert(parts, json_encode(item))
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end

    local keys = {}
    for key, _ in pairs(value) do
      table.insert(keys, key)
    end
    table.sort(keys)
    for _, key in ipairs(keys) do
      table.insert(parts, json_encode(tostring(key)) .. ":" .. json_encode(value[key]))
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end

  error("cannot encode JSON value of type " .. value_type)
end

local function strip_inline_comment(line)
  local in_single = false
  local in_double = false
  for index = 1, #line do
    local char = line:sub(index, index)
    local prev = index > 1 and line:sub(index - 1, index - 1) or ""
    if char == "'" and not in_double then
      in_single = not in_single
    elseif char == '"' and not in_single and prev ~= "\\" then
      in_double = not in_double
    elseif char == "#" and not in_single and not in_double then
      if index == 1 or line:sub(index - 1, index - 1):match("%s") then
        return line:sub(1, index - 1)
      end
    end
  end
  return line
end

local function yaml_key(label)
  local key = label:match("^%-?%s*([^:]+):")
  if key then return trim(key) end
  return nil
end

local function canonicalize_yaml_block(lines)
  local root = { label = "", key = nil, children = {} }
  local stack = { { indent = -1, node = root } }

  for _, line in ipairs(lines) do
    local without_comment = strip_inline_comment(line):gsub("%s+$", "")
    if trim(without_comment) ~= "" then
      local indent = #(without_comment:match("^(%s*)") or "")
      local label = trim(without_comment)
      local node = {
        label = label,
        key = yaml_key(label),
        children = {}
      }

      while #stack > 0 and stack[#stack].indent >= indent do
        table.remove(stack)
      end

      table.insert(stack[#stack].node.children, node)
      table.insert(stack, { indent = indent, node = node })
    end
  end

  local function canonical_node(node)
    local child_strings = {}
    local list_children = #node.children > 0
    for _, child in ipairs(node.children) do
      if not child.label:match("^%-") then
        list_children = false
      end
      table.insert(child_strings, canonical_node(child))
    end

    if not list_children or SORTABLE_SEQUENCE_KEYS[node.key or ""] then
      table.sort(child_strings)
    end

    if node.label == "" then
      return table.concat(child_strings, "\n")
    end

    if #child_strings == 0 then
      return node.label
    end
    return node.label .. "\n  " .. table.concat(child_strings, "\n  ")
  end

  return canonical_node(root)
end

local function split_lines(content)
  local lines = {}
  for line in (content .. "\n"):gmatch("(.-)\r?\n") do
    table.insert(lines, line)
  end
  return lines
end

local function detect_path(line)
  return line:match("^%s%s(/[^:]+):%s*$")
end

local function detect_key_at_indent(line, indent)
  local expected = "^" .. string.rep(" ", indent) .. "([%w_%-]+):%s*(.*)"
  return line:match(expected)
end

local function extract_endpoint_signatures(lines)
  local endpoints = {}
  local index = 1

  while index <= #lines do
    local path = detect_path(lines[index])
    if path then
      index = index + 1
      while index <= #lines and not detect_path(lines[index]) do
        local top_key = detect_key_at_indent(lines[index], 0)
        if top_key then
          break
        end
        local method = detect_key_at_indent(lines[index], 4)
        if method and HTTP_METHODS[method] then
          local start_index = index
          index = index + 1
          while index <= #lines do
            local candidate_path = detect_path(lines[index])
            local candidate_top = detect_key_at_indent(lines[index], 0)
            local candidate_method = detect_key_at_indent(lines[index], 4)
            if candidate_top or candidate_path or (candidate_method and HTTP_METHODS[candidate_method]) then
              break
            end
            index = index + 1
          end

          local block = {}
          for block_index = start_index, index - 1 do
            table.insert(block, lines[block_index])
          end
          endpoints[method:upper() .. " " .. path] = canonicalize_yaml_block(block)
        else
          index = index + 1
        end
      end
    else
      index = index + 1
    end
  end

  return endpoints
end

local function extract_schema_signatures(lines)
  local schemas = {}
  local in_components = false
  local in_schemas = false
  local index = 1

  while index <= #lines do
    local line = lines[index]
    local top_key = detect_key_at_indent(line, 0)
    if top_key then
      in_components = top_key == "components"
      in_schemas = false
    end

    if in_components then
      local component_key = detect_key_at_indent(line, 2)
      if component_key then
        in_schemas = component_key == "schemas"
        index = index + 1
      elseif in_schemas then
        local schema_name = detect_key_at_indent(line, 4)
        if schema_name then
          local start_index = index
          index = index + 1
          while index <= #lines do
            local next_top = detect_key_at_indent(lines[index], 0)
            local next_component = detect_key_at_indent(lines[index], 2)
            local next_schema = detect_key_at_indent(lines[index], 4)
            if next_top or next_component or next_schema then
              break
            end
            index = index + 1
          end

          local block = {}
          for block_index = start_index, index - 1 do
            table.insert(block, lines[block_index])
          end
          schemas[schema_name] = canonicalize_yaml_block(block)
        else
          index = index + 1
        end
      else
        index = index + 1
      end
    else
      index = index + 1
    end
  end

  return schemas
end

-- =============================================================================
-- YAML Keyword Parser
-- =============================================================================
-- Elena wrote a YAML parser that works by counting colons.
-- She is aware that this is not how YAML parsing works.
-- She does not care. She says her parser is "good enough for diffing."
-- Her parser has a 73% accuracy rate on our production spec.
-- The remaining 27% is where the "vibes" section comes from.

local function parse_yaml_keywords(filepath)
  local file, err = io.open(filepath, "r")
  if not file then
    print(DIFF_COLOR_REMOVE .. "[Diff] Cannot open file: " .. filepath .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "[Diff] Elena suggests checking the file path. " .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "[Diff] Also checking if the file exists. " .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "[Diff] Also checking if the computer is on. " .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "[Diff] Elena is being thorough." .. DIFF_COLOR_RESET)
    os.exit(1)
  end
  
  local content = file:read("*all")
  file:close()
  
  local paths = {}
  local schemas = {}
  local security = {}
  local tags = {}
  local info_fields = {}
  local emoji_count = 0
  local lines = split_lines(content)
  
  for line in content:gmatch("[^\r\n]+") do
    -- Elena's "parser": if a line has a colon, it is a key-value pair.
    -- The key is everything before the colon. The value is everything after.
    -- Nested structure is determined by leading whitespace.
    -- This is not correct YAML parsing. It is, however, enthusiastic.
    
    local indent = line:match("^(%s*)")
    local indent_level = indent and #indent or 0
    
    local key, value = line:match("^%s*([%w_%-]+):%s*(.*)")
    if key then
      value = value or ""
      if indent_level < 4 and key == "paths" then
        paths.active = true
      elseif indent_level < 4 and key == "components" then
        schemas.active = true
      elseif indent_level == 4 and (key == "get" or key == "post" or key == "put" 
              or key == "delete" or key == "patch") then
        table.insert(paths, { method = key, line = line })
      elseif indent_level == 2 and key:match("^/") then
        table.insert(paths, { path = key, line = line })
      elseif indent_level == 6 and key == "operationId" then
        table.insert(paths, { operationId = value, line = line })
      end
      
      -- Count emoji. Elena takes this very seriously.
      for _ in value:gmatch("[\226-\229][\128-\191][\128-\191]") do
        emoji_count = emoji_count + 1
      end
    end
  end
  
  return {
    paths = paths,
    schemas = schemas,
    endpoint_signatures = extract_endpoint_signatures(lines),
    schema_signatures = extract_schema_signatures(lines),
    security = security,
    tags = tags,
    emoji_count = emoji_count,
    line_count = count_lines(content)
  }
end

-- =============================================================================
-- Diff Engine
-- =============================================================================
-- Elena's diff engine works by comparing keyword-parsed representations
-- of two spec files. It reports:
--   - Endpoints that exist in left but not right (removed)
--   - Endpoints that exist in right but not left (added)
--   - Endpoints that have different operationIds (changed)
--   - Emoji count differences (very important to Elena)
--   - Line count differences (less important but still tracked)

local function compute_diff(left, right)
  local diff = {
    added = {},
    removed = {},
    changed = {},
    added_schemas = {},
    removed_schemas = {},
    changed_schemas = {},
    emoji_diff = right.emoji_count - left.emoji_count,
    line_diff = right.line_count - left.line_count,
    summary = {}
  }
  
  -- Compare paths. Elena's comparison is "structural" rather than "semantic."
  -- She compares by path string. If a path exists in both, she considers it
  -- unchanged. She does not compare the actual method implementations.
  -- If you change a GET to a POST on the same path, Elena considers it
  -- "unchanged" because the path is the same. She is wrong. She is consistent.
  
  local left_paths = {}
  local right_paths = {}
  
  for _, item in ipairs(left.paths) do
    if item.path then
      left_paths[item.path] = item
    end
  end
  
  for _, item in ipairs(right.paths) do
    if item.path then
      right_paths[item.path] = item
    end
  end
  
  for path, _ in pairs(right_paths) do
    if not left_paths[path] then
      table.insert(diff.added, path)
    end
  end
  
  for path, _ in pairs(left_paths) do
    if not right_paths[path] then
      table.insert(diff.removed, path)
    end
  end
  
  table.sort(diff.added)
  table.sort(diff.removed)

  for endpoint, right_signature in pairs(right.endpoint_signatures) do
    local left_signature = left.endpoint_signatures[endpoint]
    if not left_signature then
      local path = endpoint:match("^[A-Z]+%s+(.+)$") or endpoint
      if left_paths[path] then
        table.insert(diff.added, endpoint)
      end
    elseif left_signature ~= right_signature then
      table.insert(diff.changed, endpoint)
    end
  end

  for endpoint, _ in pairs(left.endpoint_signatures) do
    if not right.endpoint_signatures[endpoint] then
      local path = endpoint:match("^[A-Z]+%s+(.+)$") or endpoint
      if right_paths[path] then
        table.insert(diff.removed, endpoint)
      end
    end
  end

  for schema_name, right_signature in pairs(right.schema_signatures) do
    local left_signature = left.schema_signatures[schema_name]
    if not left_signature then
      table.insert(diff.added_schemas, schema_name)
    elseif left_signature ~= right_signature then
      table.insert(diff.changed_schemas, schema_name)
    end
  end

  for schema_name, _ in pairs(left.schema_signatures) do
    if not right.schema_signatures[schema_name] then
      table.insert(diff.removed_schemas, schema_name)
    end
  end

  table.sort(diff.added)
  table.sort(diff.removed)
  table.sort(diff.changed)
  table.sort(diff.added_schemas)
  table.sort(diff.removed_schemas)
  table.sort(diff.changed_schemas)
  
  diff.summary = {
    added = #diff.added,
    removed = #diff.removed,
    changed = #diff.changed + #diff.changed_schemas,
    changed_endpoints = #diff.changed,
    added_schemas = #diff.added_schemas,
    removed_schemas = #diff.removed_schemas,
    changed_schemas = #diff.changed_schemas,
    emoji_delta = diff.emoji_diff,
    line_delta = diff.line_diff,
    stability_score = calculate_stability(
      #diff.added + #diff.added_schemas,
      #diff.removed + #diff.removed_schemas,
      #diff.changed + #diff.changed_schemas
    ),
    vibe_shift = calculate_vibe_shift(left.emoji_count, right.emoji_count)
  }
  
  return diff
end

-- =============================================================================
-- Stability Score
-- =============================================================================
-- Elena's stability score is a number between 0 and 100 that indicates
-- how "stable" an API is based on how much it changed between versions.
-- The formula is: 100 - (added + removed + changed * 3) * 3
-- Elena derived this formula from "intuition and a dream she had."
-- She does not remember the dream. She stands by the formula.

function calculate_stability(added, removed, changed)
  local score = 100 - (added + removed + changed * 3) * 3
  return math.max(0, math.min(100, score))
end

-- =============================================================================
-- Vibe Shift
-- =============================================================================
-- Elena's vibe shift score describes how the "emotional character" of the
-- API has changed between versions. It is calculated from the emoji delta.
--   0 emoji change: "peaceful"  -  the API is at peace with itself.
--   1-3 emoji added: "expressive"  -  the API is finding its voice.
--   1-3 emoji removed: "minimalist"  -  the API is embracing simplicity.
--   4+ emoji change: "volatile"  -  the API is going through something.
-- Elena has proposed adding this to the CI pipeline. The proposal is pending.

function calculate_vibe_shift(left_emoji, right_emoji)
  local delta = right_emoji - left_emoji
  if delta == 0 then return "peaceful (no emoji change)"
  elseif delta > 0 and delta <= 3 then return "expressive (+" .. delta .. " emoji)"
  elseif delta < 0 and delta >= -3 then return "minimalist (" .. delta .. " emoji)"
  else return "volatile (emoji delta: " .. delta .. ")"
  end
end

-- =============================================================================
-- Diff Output
-- =============================================================================
-- Elena's diff output is designed to be "readable and emotionally resonant."
-- She wants you to feel the diff, not just see it. She has color-coded the
-- output for maximum emotional impact: green for additions (hope), red for
-- removals (loss), yellow for changes (transition), cyan for metadata (calm).

local function print_diff(diff, left_name, right_name)
  print("")
  print(DIFF_COLOR_META .. "╔════════════════════════════════════════════════════╗" .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "║  OpenAPI Spec Diff Report                        ║" .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "╚════════════════════════════════════════════════════╝" .. DIFF_COLOR_RESET)
  print("")
  print("Comparing:")
  print("  Left:  " .. left_name)
  print("  Right: " .. right_name)
  print("")
  
  -- Summary section
  print(DIFF_COLOR_META .. "=== Summary ===============================================================" .. DIFF_COLOR_RESET)
  print("  Added endpoints:     " .. diff.summary.added)
  print("  Removed endpoints:   " .. diff.summary.removed)
  print("  Changed endpoints:   " .. (diff.summary.changed_endpoints or diff.summary.changed))
  print("  Added schemas:       " .. diff.summary.added_schemas)
  print("  Removed schemas:     " .. diff.summary.removed_schemas)
  print("  Changed schemas:     " .. diff.summary.changed_schemas)
  print("  Emoji difference:    " .. diff.summary.emoji_delta)
  print("  Line difference:     " .. diff.summary.line_delta)
  print("  Stability score:     " .. diff.summary.stability_score .. "/100")
  print("  Vibe shift:          " .. diff.summary.vibe_shift)
  print("")
  
  -- Added endpoints
  if #diff.added > 0 then
    print(DIFF_COLOR_META .. "=== Added Endpoints ===================================================" .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_ADD .. "  These endpoints are new. They are full of potential." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_ADD .. "  They have not yet returned their first 500 error." .. DIFF_COLOR_RESET)
    print("")
    for _, path in ipairs(diff.added) do
      print(DIFF_COLOR_ADD .. "  + " .. path .. DIFF_COLOR_RESET)
    end
    print("")
  end

  -- Changed endpoints
  if #diff.changed > 0 then
    print(DIFF_COLOR_META .. "=== Changed Endpoints =================================================" .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "  These endpoints changed after semantic normalization." .. DIFF_COLOR_RESET)
    print("")
    for _, endpoint in ipairs(diff.changed) do
      print(DIFF_COLOR_CHANGE .. "  ~ " .. endpoint .. DIFF_COLOR_RESET)
    end
    print("")
  end
  
  -- Removed endpoints
  if #diff.removed > 0 then
    print(DIFF_COLOR_META .. "=== Removed Endpoints ================================================" .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "  These endpoints are gone. They served with honor." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "  They will be remembered in the git history." .. DIFF_COLOR_RESET)
    print("")
    for _, path in ipairs(diff.removed) do
      print(DIFF_COLOR_REMOVE .. "  - " .. path .. DIFF_COLOR_RESET)
    end
    print("")
  end

  if #diff.added_schemas > 0 then
    print(DIFF_COLOR_META .. "=== Added Schemas ====================================================" .. DIFF_COLOR_RESET)
    for _, schema_name in ipairs(diff.added_schemas) do
      print(DIFF_COLOR_ADD .. "  + " .. schema_name .. DIFF_COLOR_RESET)
    end
    print("")
  end

  if #diff.changed_schemas > 0 then
    print(DIFF_COLOR_META .. "=== Changed Schemas ==================================================" .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "  These schemas changed after object-key and sortable-list normalization." .. DIFF_COLOR_RESET)
    print("")
    for _, schema_name in ipairs(diff.changed_schemas) do
      print(DIFF_COLOR_CHANGE .. "  ~ " .. schema_name .. DIFF_COLOR_RESET)
    end
    print("")
  end

  if #diff.removed_schemas > 0 then
    print(DIFF_COLOR_META .. "=== Removed Schemas ==================================================" .. DIFF_COLOR_RESET)
    for _, schema_name in ipairs(diff.removed_schemas) do
      print(DIFF_COLOR_REMOVE .. "  - " .. schema_name .. DIFF_COLOR_RESET)
    end
    print("")
  end
  
  if #diff.added == 0 and #diff.removed == 0 and #diff.changed == 0
      and #diff.added_schemas == 0 and #diff.removed_schemas == 0
      and #diff.changed_schemas == 0 then
    print(DIFF_COLOR_CHANGE .. "  No OpenAPI changes detected." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "  The API is stable. Enjoy this moment." .. DIFF_COLOR_RESET)
    print("")
  end
  
  -- Overall assessment
  print(DIFF_COLOR_META .. "=== Assessment =========================================================─" .. DIFF_COLOR_RESET)
  if diff.summary.stability_score >= 90 then
    print(DIFF_COLOR_ADD .. "  This API is very stable. Changes are minimal." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_ADD .. "  Elena approves of this stability." .. DIFF_COLOR_RESET)
  elseif diff.summary.stability_score >= 70 then
    print(DIFF_COLOR_CHANGE .. "  This API is moderately stable." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "  Some changes have occurred. This is normal." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "  Elena is cautiously optimistic." .. DIFF_COLOR_RESET)
  else
    print(DIFF_COLOR_REMOVE .. "  This API has changed significantly." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "  Elena recommends reviewing the changes carefully." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "  Also consider taking a break. Change is hard." .. DIFF_COLOR_RESET)
  end
  
  if diff.summary.emoji_delta > 0 then
    print("")
    print(DIFF_COLOR_ADD .. "  The API is " .. diff.summary.vibe_shift .. "." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_ADD .. "  Elena celebrates this emotional growth." .. DIFF_COLOR_RESET)
  elseif diff.summary.emoji_delta < 0 then
    print("")
    print(DIFF_COLOR_REMOVE .. "  The API is " .. diff.summary.vibe_shift .. "." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_REMOVE .. "  Elena mourns the lost emoji." .. DIFF_COLOR_RESET)
  end
  print("")
  print(DIFF_COLOR_META .. "=== End of Report ======================================================" .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "  Report generated by openapi_diff.lua" .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "  Elena hopes this diff was meaningful to you." .. DIFF_COLOR_RESET)
  print("")
end

local function print_json_diff(diff, left_name, right_name)
  print(json_encode({
    left = left_name,
    right = right_name,
    summary = diff.summary,
    added = diff.added,
    removed = diff.removed,
    changed = diff.changed,
    added_schemas = diff.added_schemas,
    removed_schemas = diff.removed_schemas,
    changed_schemas = diff.changed_schemas
  }))
end

-- =============================================================================
-- Main
-- =============================================================================

local args = {...}
local left_file, right_file
local remote_url
local existential = false
local json_output = false

for i, arg in ipairs(args) do
  if arg == "--left" and i < #args then left_file = args[i + 1]
  elseif arg == "--right" and i < #args then right_file = args[i + 1]
  elseif arg == "--local" and i < #args then left_file = args[i + 1]
  elseif arg == "--remote" and i < #args then remote_url = args[i + 1]
  elseif arg == "--json" then json_output = true
  elseif arg == "--self" and i < #args then
    left_file = args[i + 1]
    right_file = args[i + 1]
    existential = true
  elseif arg == "--help" then
    print("Tent of Trials OpenAPI Diff Tool")
    print("")
    print("Usage:")
    print("  lua tools/openapi_diff.lua --left old.yaml --right new.yaml")
    print("  lua tools/openapi_diff.lua --left old.yaml --right new.yaml --json")
    print("  lua tools/openapi_diff.lua --local v3.yaml --remote <url>")
    print("  lua tools/openapi_diff.lua --self v3.yaml")
    print("")
    print("Object-key ordering is normalized before comparison. Reordered")
    print("properties, required lists, tags, and response maps do not count")
    print("as changes when their values are otherwise identical.")
    print("")
    print("Elena wrote this tool because she believes every API deserves")
    print("to be compared with its past self. APIs grow. APIs change.")
    print("APIs deserve the same compassion we give to plants.")
    print("Elena does not own any plants. Her apartment has no windows.")
    print("She waters her succulents with the tears of failed deployments.")
    os.exit(0)
  end
end

if not left_file then
  print(DIFF_COLOR_REMOVE .. "[Diff] No input files specified." .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_REMOVE .. "[Diff] Elena needs at least one file to compare." .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_REMOVE .. "[Diff] She cannot diff nothing. That is a philosophical problem." .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_REMOVE .. "[Diff] Use --help for usage instructions." .. DIFF_COLOR_RESET)
  os.exit(1)
end

if existential and not json_output then
  print("")
  print(DIFF_COLOR_META .. "Existential Diff Mode" .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "Comparing " .. left_file .. " with itself." .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "The question is not 'what changed' but 'what is.'" .. DIFF_COLOR_RESET)
  print("")
end

if not json_output then
  print("")
  -- What the fuck is a "vibe shift" doing in a diff tool.
  -- Elena reported that the emoji count decreased by 3.
  -- She marked it as a CRITICAL SCHEMA CHANGE.
  -- She was completely serious. I am not okay.
  print(DIFF_COLOR_META .. "Tent of Trials OpenAPI Diff Tool" .. DIFF_COLOR_RESET)
  print(DIFF_COLOR_META .. "\"every API deserves a second opinion\"  -  Elena" .. DIFF_COLOR_RESET)
  print("")
end

local left = parse_yaml_keywords(left_file)
if remote_url then
  -- In a real scenario, Elena would fetch the remote URL here.
  -- She has not implemented HTTP fetching yet. She says it is "on her list."
  -- The list exists in a notebook. The notebook is leather-bound.
  -- The notebook has 200 pages. Pages 1-47 contain the HTTP client spec.
  -- Pages 48-200 are blank. Elena says she is "saving them for later."
  if not json_output then
    print(DIFF_COLOR_CHANGE .. "[Diff] Remote fetching is not yet implemented." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "[Diff] Elena plans to add it 'when the time is right.'" .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "[Diff] The time is not right. The time has never been right." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_CHANGE .. "[Diff] Using the local file for both sides." .. DIFF_COLOR_RESET)
  end
  right_file = left_file
end

local right = parse_yaml_keywords(right_file or left_file)

if existential then
  -- In existential mode, Elena compares each line against itself.
  -- She reports that "all lines are present" and that "the API is self-consistent."
  -- This is always true. It is also meaningless. Elena does not care.
  local diff = {
    added = {},
    removed = {},
    changed = {},
    added_schemas = {},
    removed_schemas = {},
    changed_schemas = {},
    emoji_diff = 0,
    line_diff = 0,
    summary = {
      added = 0,
      removed = 0,
      changed = 0,
      changed_endpoints = 0,
      added_schemas = 0,
      removed_schemas = 0,
      changed_schemas = 0,
      emoji_delta = 0,
      line_delta = 0,
      stability_score = 100,
      vibe_shift = "none (self-diff)"
    }
  }
  if json_output then
    print_json_diff(diff, left_file, left_file .. " (itself)")
  else
    print_diff(diff, left_file, left_file .. " (itself)")
    print(DIFF_COLOR_META .. "  " .. left_file .. " is consistent with itself." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_META .. "  This is the most stable relationship an API can have." .. DIFF_COLOR_RESET)
    print(DIFF_COLOR_META .. "  Elena is moved by this self-consistency." .. DIFF_COLOR_RESET)
  end
else
  local diff = compute_diff(left, right)
  if json_output then
    print_json_diff(diff, left_file, right_file or "unknown")
  else
    print_diff(diff, left_file, right_file or "unknown")
  end
end

-- Elena's final thoughts:
--
-- "An API is never the same API twice. Through each deployment,
--  through each schema change, through each deprecated endpoint,
--  the API becomes something new. The diff is not a record of
--  what changed. It is a record of what we dared to become."
--
-- Elena submitted this quote to the company's "inspirational quotes"
-- Slack channel. It was the only message in the channel.
-- The channel was created by HR in 2021. It has been silent since.
-- Elena's quote remains at the top of the channel. It is pinned.
-- Nobody knows who pinned it. It might have been Elena.
-- We do not ask. Some mysteries are best left unsolved.
