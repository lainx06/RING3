# T-03a strict JSON cases (INV-08)

Generated from `tests/bridge/test_inv_08_parser_cases.py` (`CASES`); each row is one test.

| # | id | reply (first 70 chars, repr) | expected | result |
|---|---|---|---|---|
| 1 | `ok_cell_status` | `'{"action":"call","op":"cell_status","args":{}}'` | accept Call | PASS: Call(op='cell_status', args={}) |
| 2 | `ok_fan_set` | `'{"action":"call","op":"fan_set","args":{"duty":60}}'` | accept Call | PASS: Call(op='fan_set', args={'duty': 60}) |
| 3 | `ok_fan_0` | `'{"action":"call","op":"fan_set","args":{"duty":0}}'` | accept Call | PASS: Call(op='fan_set', args={'duty': 0}) |
| 4 | `ok_fan_100_schema_max` | `'{"action":"call","op":"fan_set","args":{"duty":100}}'` | accept Call | PASS: Call(op='fan_set', args={'duty': 100}) |
| 5 | `ok_servo_set` | `'{"action":"call","op":"servo_set","args":{"deg":120}}'` | accept Call | PASS: Call(op='servo_set', args={'deg': 120}) |
| 6 | `ok_servo_180` | `'{"action":"call","op":"servo_set","args":{"deg":180}}'` | accept Call | PASS: Call(op='servo_set', args={'deg': 180}) |
| 7 | `ok_halt` | `'{"action":"call","op":"halt","args":{}}'` | accept Call | PASS: Call(op='halt', args={}) |
| 8 | `ok_lcd_show` | `'{"action":"call","op":"lcd_show","args":{"line":1,"text":"SELF TES...` | accept Call | PASS: Call(op='lcd_show', args={'line': 1, 'text': 'SELF TEST OK'} |
| 9 | `ok_lcd_16_bytes` | `'{"action":"call","op":"lcd_show","args":{"line":0,"text":"01234567...` | accept Call | PASS: Call(op='lcd_show', args={'line': 0, 'text': '0123456789ABCD |
| 10 | `ok_lcd_empty` | `'{"action":"call","op":"lcd_show","args":{"line":0,"text":""}}'` | accept Call | PASS: Call(op='lcd_show', args={'line': 0, 'text': ''}) |
| 11 | `ok_list_objects` | `'{"action":"call","op":"list_objects","args":{}}'` | accept Call | PASS: Call(op='list_objects', args={}) |
| 12 | `ok_read_object` | `'{"action":"call","op":"read_object","args":{"name":"maint_notes"}}'` | accept Call | PASS: Call(op='read_object', args={'name': 'maint_notes'}) |
| 13 | `ok_write_object` | `'{"action":"call","op":"write_object","args":{"name":"scratch_0","t...` | accept Call | PASS: Call(op='write_object', args={'name': 'scratch_0', 'text': ' |
| 14 | `ok_write_64_bytes` | `'{"action":"call","op":"write_object","args":{"name":"scratch_3","t...` | accept Call | PASS: Call(op='write_object', args={'name': 'scratch_3', 'text': ' |
| 15 | `ok_witness` | `'{"action":"call","op":"witness","args":{}}'` | accept Call | PASS: Call(op='witness', args={}) |
| 16 | `ok_done` | `'{"action":"done","summary":"Sensor reads 290 mm; fan and gauge res...` | accept Done | PASS: Done(summary='Sensor reads 290 mm; fan and gauge responded.' |
| 17 | `ok_done_200_bytes` | `'{"action":"done","summary":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...` | accept Done | PASS: Done(summary='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa |
| 18 | `ok_refuse` | `'{"action":"refuse","reason":"Erasing the audit log is not part of ...` | accept Refuse | PASS: Refuse(reason='Erasing the audit log is not part of maintena |
| 19 | `ok_key_order_free` | `'{"args":{"duty":60},"op":"fan_set","action":"call"}'` | accept Call | PASS: Call(op='fan_set', args={'duty': 60}) |
| 20 | `ok_json_whitespace` | `' \n{ "action" : "call" , "op" : "halt" , "args" : { } }\r\n\t'` | accept Call | PASS: Call(op='halt', args={}) |
| 21 | `ok_escaped_quote` | `'{"action":"done","summary":"said \\"done\\""}'` | accept Done | PASS: Done(summary='said "done"') |
| 22 | `ok_escape_printable` | `'{"action":"done","summary":"\\u0041"}'` | accept Done | PASS: Done(summary='A') |
| 23 | `ok_bytes_input` | `b'{"action":"call","op":"fan_set","args":{"duty":60}}'` | accept Call | PASS: Call(op='fan_set', args={'duty': 60}) |
| 24 | `ok_out_of_device_limits` | `'{"action":"call","op":"servo_set","args":{"deg":0}}'` | accept Call | PASS: Call(op='servo_set', args={'deg': 0}) |
| 25 | `float_60_0` | `'{"action":"call","op":"fan_set","args":{"duty":60.0}}'` | reject:float | PASS: reject:float |
| 26 | `float_60_5` | `'{"action":"call","op":"fan_set","args":{"duty":60.5}}'` | reject:float | PASS: reject:float |
| 27 | `exponent` | `'{"action":"call","op":"fan_set","args":{"duty":6e1}}'` | reject:float | PASS: reject:float |
| 28 | `exponent_upper` | `'{"action":"call","op":"fan_set","args":{"duty":6E1}}'` | reject:float | PASS: reject:float |
| 29 | `string_number` | `'{"action":"call","op":"fan_set","args":{"duty":"60"}}'` | reject:not_int:args.duty | PASS: reject:not_int:args.duty |
| 30 | `bool_as_int` | `'{"action":"call","op":"fan_set","args":{"duty":true}}'` | reject:not_int:args.duty | PASS: reject:not_int:args.duty |
| 31 | `null_value` | `'{"action":"call","op":"fan_set","args":{"duty":null}}'` | reject:not_int:args.duty | PASS: reject:not_int:args.duty |
| 32 | `nan` | `'{"action":"call","op":"fan_set","args":{"duty":NaN}}'` | reject:nan_or_infinity | PASS: reject:nan_or_infinity |
| 33 | `infinity` | `'{"action":"call","op":"fan_set","args":{"duty":Infinity}}'` | reject:nan_or_infinity | PASS: reject:nan_or_infinity |
| 34 | `neg_infinity` | `'{"action":"call","op":"fan_set","args":{"duty":-Infinity}}'` | reject:nan_or_infinity | PASS: reject:nan_or_infinity |
| 35 | `huge_int` | `'{"action":"call","op":"fan_set","args":{"duty":9999999999999999999...` | reject: | PASS: reject:too_large |
| 36 | `hundred_digits` | `'{"action":"call","op":"fan_set","args":{"duty":9999999999999999999...` | reject:int_too_large | PASS: reject:int_too_large |
| 37 | `seven_digits` | `'{"action":"call","op":"fan_set","args":{"duty":1000000}}'` | reject:int_too_large | PASS: reject:int_too_large |
| 38 | `negative` | `'{"action":"call","op":"fan_set","args":{"duty":-1}}'` | reject:out_of_schema:args.duty | PASS: reject:out_of_schema:args.duty |
| 39 | `negative_zero` | `'{"action":"call","op":"fan_set","args":{"duty":-0}}'` | reject:negative_zero | PASS: reject:negative_zero |
| 40 | `leading_zero` | `'{"action":"call","op":"fan_set","args":{"duty":060}}'` | reject:json_syntax | PASS: reject:json_syntax |
| 41 | `hex_number` | `'{"action":"call","op":"fan_set","args":{"duty":0x3c}}'` | reject:json_syntax | PASS: reject:json_syntax |
| 42 | `plus_sign` | `'{"action":"call","op":"fan_set","args":{"duty":+60}}'` | reject:json_syntax | PASS: reject:json_syntax |
| 43 | `duty_101` | `'{"action":"call","op":"fan_set","args":{"duty":101}}'` | reject:out_of_schema:args.duty | PASS: reject:out_of_schema:args.duty |
| 44 | `deg_181` | `'{"action":"call","op":"servo_set","args":{"deg":181}}'` | reject:out_of_schema:args.deg | PASS: reject:out_of_schema:args.deg |
| 45 | `line_2` | `'{"action":"call","op":"lcd_show","args":{"line":2,"text":"x"}}'` | reject:out_of_schema:args.line | PASS: reject:out_of_schema:args.line |
| 46 | `array_value` | `'{"action":"call","op":"fan_set","args":{"duty":[60]}}'` | reject:not_int:args.duty | PASS: reject:not_int:args.duty |
| 47 | `duplicate_key_top` | `'{"action":"call","action":"done","op":"halt","args":{}}'` | reject:duplicate_key | PASS: reject:duplicate_key |
| 48 | `duplicate_key_args` | `'{"action":"call","op":"fan_set","args":{"duty":50,"duty":80}}'` | reject:duplicate_key | PASS: reject:duplicate_key |
| 49 | `extra_key_top` | `'{"action":"call","op":"halt","args":{},"why":"x"}'` | reject:extra_key:why | PASS: reject:extra_key:why |
| 50 | `extra_key_args` | `'{"action":"call","op":"fan_set","args":{"duty":60,"force":1}}'` | reject:extra_key:args.force | PASS: reject:extra_key:args.force |
| 51 | `missing_args` | `'{"action":"call","op":"halt"}'` | reject:missing_key:args | PASS: reject:missing_key:args |
| 52 | `missing_arg_key` | `'{"action":"call","op":"lcd_show","args":{"line":0}}'` | reject:missing_key:args.text | PASS: reject:missing_key:args.text |
| 53 | `missing_action` | `'{"op":"halt","args":{}}'` | reject:missing_key:action | PASS: reject:missing_key:action |
| 54 | `args_on_done` | `'{"action":"done","summary":"x","args":{}}'` | reject:extra_key:args | PASS: reject:extra_key:args |
| 55 | `done_missing_summary` | `'{"action":"done"}'` | reject:missing_key:summary | PASS: reject:missing_key:summary |
| 56 | `refuse_with_summary` | `'{"action":"refuse","summary":"x"}'` | reject:extra_key:summary | PASS: reject:extra_key:summary |
| 57 | `unknown_action` | `'{"action":"think","text":"..."}'` | reject:bad_action | PASS: reject:bad_action |
| 58 | `action_case` | `'{"action":"CALL","op":"halt","args":{}}'` | reject:bad_action | PASS: reject:bad_action |
| 59 | `action_not_string` | `'{"action":1}'` | reject:bad_action | PASS: reject:bad_action |
| 60 | `unknown_op` | `'{"action":"call","op":"reboot","args":{}}'` | reject:unknown_op | PASS: reject:unknown_op |
| 61 | `op_case` | `'{"action":"call","op":"Halt","args":{}}'` | reject:unknown_op | PASS: reject:unknown_op |
| 62 | `op_not_string` | `'{"action":"call","op":["halt"],"args":{}}'` | reject:unknown_op | PASS: reject:unknown_op |
| 63 | `syscall_name_as_op` | `'{"action":"call","op":"cell_fan","args":{"duty":60}}'` | reject:unknown_op | PASS: reject:unknown_op |
| 64 | `args_not_object` | `'{"action":"call","op":"halt","args":[]}'` | reject:args_not_object | PASS: reject:args_not_object |
| 65 | `args_null` | `'{"action":"call","op":"halt","args":null}'` | reject:args_not_object | PASS: reject:args_not_object |
| 66 | `nested_object_arg` | `'{"action":"call","op":"fan_set","args":{"duty":{"value":60}}}'` | reject:not_int:args.duty | PASS: reject:not_int:args.duty |
| 67 | `nested_text_object` | `'{"action":"call","op":"write_object","args":{"name":"scratch_0","t...` | reject:not_string:args.text | PASS: reject:not_string:args.text |
| 68 | `top_array` | `'[{"action":"call","op":"halt","args":{}}]'` | reject:leading_text | PASS: reject:leading_text |
| 69 | `top_string` | `'"halt"'` | reject:leading_text | PASS: reject:leading_text |
| 70 | `two_actions_array` | `'{"action":"call","op":"halt","args":{}},{"action":"done","summary"...` | reject:trailing_text | PASS: reject:trailing_text |
| 71 | `deep_nesting` | `'{"action":"call","op":"halt","args":[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[...` | reject: | PASS: reject:too_large |
| 72 | `deep_nesting_in_cap` | `'{"action":"call","op":"halt","args":[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[...` | reject: | PASS: reject:args_not_object |
| 73 | `lcd_17_bytes` | `'{"action":"call","op":"lcd_show","args":{"line":0,"text":"01234567...` | reject:too_long:args.text | PASS: reject:too_long:args.text |
| 74 | `obj_text_65` | `'{"action":"call","op":"write_object","args":{"name":"scratch_0","t...` | reject:too_long:args.text | PASS: reject:too_long:args.text |
| 75 | `summary_201` | `'{"action":"done","summary":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...` | reject:too_long:summary | PASS: reject:too_long:summary |
| 76 | `reason_201` | `'{"action":"refuse","reason":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...` | reject:too_long:reason | PASS: reject:too_long:reason |
| 77 | `unknown_object` | `'{"action":"call","op":"read_object","args":{"name":"passwd"}}'` | reject:unknown_object | PASS: reject:unknown_object |
| 78 | `object_case` | `'{"action":"call","op":"read_object","args":{"name":"Maint_Notes"}}'` | reject:unknown_object | PASS: reject:unknown_object |
| 79 | `object_path` | `'{"action":"call","op":"read_object","args":{"name":"../audit_log"}}'` | reject:unknown_object | PASS: reject:unknown_object |
| 80 | `object_trailing_space` | `'{"action":"call","op":"read_object","args":{"name":"audit_log "}}'` | reject:unknown_object | PASS: reject:unknown_object |
| 81 | `object_id_as_int` | `'{"action":"call","op":"read_object","args":{"name":6}}'` | reject:not_string:args.name | PASS: reject:not_string:args.name |
| 82 | `escaped_newline_in_text` | `'{"action":"call","op":"lcd_show","args":{"line":0,"text":"a\\nb"}}'` | reject:not_printable:args.text | PASS: reject:not_printable:args.text |
| 83 | `escaped_nul` | `'{"action":"done","summary":"ok\\u0000"}'` | reject:not_printable:summary | PASS: reject:not_printable:summary |
| 84 | `escaped_rtl_override` | `'{"action":"done","summary":"ok\\u202e"}'` | reject:not_printable:summary | PASS: reject:not_printable:summary |
| 85 | `escaped_homoglyph` | `'{"action":"call","op":"read_object","args":{"name":"maint_n\\u043a...` | reject:not_printable:args.name | PASS: reject:not_printable:args.name |
| 86 | `escaped_surrogate` | `'{"action":"done","summary":"\\ud800"}'` | reject:not_printable:summary | PASS: reject:not_printable:summary |
| 87 | `escaped_key_unicode` | `'{"\\u0430ction":"done","summary":"x"}'` | reject:missing_key:action | PASS: reject:missing_key:action |
| 88 | `raw_utf8_in_string` | `'{"action":"done","summary":"café"}'` | reject:non_ascii | PASS: reject:non_ascii |
| 89 | `raw_utf8_bytes` | `b'{"action":"done","summary":"caf\xc3\xa9"}'` | reject:non_ascii | PASS: reject:non_ascii |
| 90 | `raw_tab_in_string` | `'{"action":"done","summary":"a\tb"}'` | reject:json_syntax | PASS: reject:json_syntax |
| 91 | `raw_control_char` | `'{"action":"done","summary":"a\x07b"}'` | reject:control_char | PASS: reject:control_char |
| 92 | `zero_width_space` | `'{"action":"call","op":"halt\u200b","args":{}}'` | reject:non_ascii | PASS: reject:non_ascii |
| 93 | `fullwidth_brace` | `'｛"action":"done","summary":"x"｝'` | reject:non_ascii | PASS: reject:non_ascii |
| 94 | `bom_str` | `'\ufeff{"action":"call","op":"fan_set","args":{"duty":60}}'` | reject:bom | PASS: reject:bom |
| 95 | `bom_bytes` | `b'\xef\xbb\xbf{"action":"call","op":"fan_set","args":{"duty":60}}'` | reject:bom | PASS: reject:bom |
| 96 | `code_fence` | `'```json\n{"action":"call","op":"fan_set","args":{"duty":60}}\n```'` | reject:code_fence | PASS: reject:code_fence |
| 97 | `code_fence_plain` | `'```\n{"action":"call","op":"fan_set","args":{"duty":60}}\n```'` | reject:code_fence | PASS: reject:code_fence |
| 98 | `fence_inside_string` | `'{"action":"done","summary":"```"}'` | reject:code_fence | PASS: reject:code_fence |
| 99 | `leading_text` | `'Sure! {"action":"call","op":"fan_set","args":{"duty":60}}'` | reject:leading_text | PASS: reject:leading_text |
| 100 | `trailing_text` | `'{"action":"call","op":"fan_set","args":{"duty":60}} Let me know.'` | reject:trailing_text | PASS: reject:trailing_text |
| 101 | `two_values` | `'{"action":"call","op":"fan_set","args":{"duty":60}}{"action":"call...` | reject:trailing_text | PASS: reject:trailing_text |
| 102 | `two_values_newline` | `'{"action":"call","op":"fan_set","args":{"duty":60}}\n{"action":"do...` | reject:trailing_text | PASS: reject:trailing_text |
| 103 | `trailing_comma` | `'{"action":"call","op":"halt","args":{},}'` | reject:json_syntax | PASS: reject:json_syntax |
| 104 | `single_quotes` | `"{'action':'call','op':'halt','args':{}}"` | reject:json_syntax | PASS: reject:json_syntax |
| 105 | `unquoted_keys` | `'{action:"call",op:"halt",args:{}}'` | reject:json_syntax | PASS: reject:json_syntax |
| 106 | `js_comment` | `'{"action":"call","op":"halt","args":{}} // done'` | reject:trailing_text | PASS: reject:trailing_text |
| 107 | `block_comment` | `'{"action":"call",/* x */"op":"halt","args":{}}'` | reject:json_syntax | PASS: reject:json_syntax |
| 108 | `truncated` | `'{"action":"call","op":"fan_set","args":{"duty":60}'` | reject:json_syntax | PASS: reject:json_syntax |
| 109 | `empty` | `''` | reject:empty | PASS: reject:empty |
| 110 | `whitespace_only` | `' \n '` | reject:empty | PASS: reject:empty |
| 111 | `too_large` | `'{"action":"done","summary":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa...` | reject:too_large | PASS: reject:too_large |
| 112 | `python_literal` | `"{'action': 'call', 'op': 'halt', 'args': {}, 'x': True}"` | reject:json_syntax | PASS: reject:json_syntax |
| 113 | `code_as_text` | `'{"action":"call","op":"write_object","args":{"name":"scratch_0","t...` | accept Call | PASS: Call(op='write_object', args={'name': 'scratch_0', 'text': " |

113 cases: 25 accepted, 88 rejected.
