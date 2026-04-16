{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ SelfTest.pas - Self-test script for the Altium integration bridge                          }
{ Run inside Altium Designer to validate every command handler.                }
{                                                                             }
{ Compile order: Main > Utils > Application > Project > Library > PCBGeneric  }
{                > Generic > Dispatcher > SelfTest                            }
{                                                                             }
{ Entry point: RunSelfTest                                                    }
{..............................................................................}

Var
    SelfTest_Passed : Integer;
    SelfTest_Failed : Integer;
    SelfTest_Log    : String;

{..............................................................................}
{ Test Assertion Helpers                                                       }
{..............................................................................}

Procedure AssertTrue(Condition : Boolean; TestName : String);
Begin
    If Condition Then
        SelfTest_Passed := SelfTest_Passed + 1
    Else
    Begin
        SelfTest_Failed := SelfTest_Failed + 1;
        SelfTest_Log := SelfTest_Log + 'FAIL: ' + TestName + #13#10;
    End;
End;

Procedure AssertEqual(Actual, Expected, TestName : String);
Begin
    If Actual = Expected Then
        SelfTest_Passed := SelfTest_Passed + 1
    Else
    Begin
        SelfTest_Failed := SelfTest_Failed + 1;
        SelfTest_Log := SelfTest_Log + 'FAIL: ' + TestName
            + ' (expected "' + Expected + '", got "' + Actual + '")' + #13#10;
    End;
End;

Procedure AssertContains(S, Substr, TestName : String);
Begin
    AssertTrue(Pos(Substr, S) > 0,
        TestName + ' should contain "' + Substr + '"');
End;

Procedure AssertNotEmpty(S : String; TestName : String);
Begin
    AssertTrue(Length(S) > 0, TestName + ' should not be empty');
End;

Procedure AssertValidJson(S : String; TestName : String);
Begin
    AssertTrue((Pos('{', S) > 0) And (Pos('"id"', S) > 0),
        TestName + ' should be valid JSON response');
End;

Procedure AssertIntEqual(Actual, Expected : Integer; TestName : String);
Begin
    If Actual = Expected Then
        SelfTest_Passed := SelfTest_Passed + 1
    Else
    Begin
        SelfTest_Failed := SelfTest_Failed + 1;
        SelfTest_Log := SelfTest_Log + 'FAIL: ' + TestName
            + ' (expected ' + IntToStr(Expected) + ', got ' + IntToStr(Actual) + ')' + #13#10;
    End;
End;

{..............................................................................}
{ 1. JSON Parsing Tests                                                        }
{..............................................................................}

Procedure TestJsonParsing;
Var
    Json : String;
Begin
    // Basic string extraction
    Json := '{"name":"hello","value":"world"}';
    AssertEqual(ExtractJsonValue(Json, 'name'), 'hello', 'ExtractJsonValue basic string');
    AssertEqual(ExtractJsonValue(Json, 'value'), 'world', 'ExtractJsonValue second key');
    AssertEqual(ExtractJsonValue(Json, 'missing'), '', 'ExtractJsonValue missing key');

    // Number extraction
    Json := '{"count":42,"enabled":true}';
    AssertEqual(ExtractJsonValue(Json, 'count'), '42', 'ExtractJsonValue number');
    AssertEqual(ExtractJsonValue(Json, 'enabled'), 'true', 'ExtractJsonValue boolean');

    // Escaped quotes in value
    Json := '{"msg":"say \"hello\""}';
    AssertEqual(ExtractJsonValue(Json, 'msg'), 'say \"hello\"', 'ExtractJsonValue escaped quotes');

    // Windows path (escaped backslashes)
    Json := '{"path":"C:\\Users\\test\\file.txt"}';
    AssertEqual(ExtractJsonValue(Json, 'path'), 'C:\\Users\\test\\file.txt', 'ExtractJsonValue Windows path');

    // Nested object — should contain inner content
    Json := '{"outer":{"inner":"value"}}';
    AssertTrue(Pos('inner', ExtractJsonValue(Json, 'outer')) > 0, 'ExtractJsonValue nested object');

    // Empty string value
    Json := '{"empty":""}';
    AssertEqual(ExtractJsonValue(Json, 'empty'), '', 'ExtractJsonValue empty string');

    // Real request format
    Json := '{"id":"abc-123","command":"application.ping","params":"{}"}';
    AssertEqual(ExtractJsonValue(Json, 'id'), 'abc-123', 'ExtractJsonValue real request id');
    AssertEqual(ExtractJsonValue(Json, 'command'), 'application.ping', 'ExtractJsonValue real request command');
    AssertEqual(ExtractJsonValue(Json, 'params'), '{}', 'ExtractJsonValue real request params');

    // Null value
    Json := '{"val":null,"next":"ok"}';
    AssertEqual(ExtractJsonValue(Json, 'val'), 'null', 'ExtractJsonValue null value');
    AssertEqual(ExtractJsonValue(Json, 'next'), 'ok', 'ExtractJsonValue after null');

    // Negative number
    Json := '{"x":-100,"y":200}';
    AssertEqual(ExtractJsonValue(Json, 'x'), '-100', 'ExtractJsonValue negative number');
    AssertEqual(ExtractJsonValue(Json, 'y'), '200', 'ExtractJsonValue positive after negative');
End;

{..............................................................................}
{ 2. JSON Array Extraction Tests                                               }
{..............................................................................}

Procedure TestJsonArrayExtraction;
Var
    Json, Res : String;
Begin
    Json := '{"items":["a","b","c"],"count":3}';
    Res := ExtractJsonArray(Json, 'items');
    AssertTrue(Pos('[', Res) > 0, 'ExtractJsonArray returns array');
    AssertTrue(Pos('"a"', Res) > 0, 'ExtractJsonArray contains first element');
    AssertTrue(Pos('"c"', Res) > 0, 'ExtractJsonArray contains last element');

    // Missing key
    AssertEqual(ExtractJsonArray(Json, 'missing'), '', 'ExtractJsonArray missing key');

    // Empty array
    Json := '{"list":[]}';
    Res := ExtractJsonArray(Json, 'list');
    AssertEqual(Res, '[]', 'ExtractJsonArray empty array');

    // Nested array
    Json := '{"data":[[1,2],[3,4]]}';
    Res := ExtractJsonArray(Json, 'data');
    AssertTrue(Pos('[1,2]', Res) > 0, 'ExtractJsonArray nested arrays');
End;

{..............................................................................}
{ 3. JSON Escaping Tests                                                       }
{..............................................................................}

Procedure TestJsonEscaping;
Begin
    AssertEqual(EscapeJsonString('hello'), 'hello', 'EscapeJsonString no escaping');
    AssertEqual(EscapeJsonString(''), '', 'EscapeJsonString empty');
    AssertEqual(EscapeJsonString('C:\path'), 'C:\\path', 'EscapeJsonString backslash');
    AssertEqual(EscapeJsonString('say "hi"'), 'say \"hi\"', 'EscapeJsonString quotes');
    AssertEqual(EscapeJsonString('line1' + #13 + #10 + 'line2'), 'line1\r\nline2', 'EscapeJsonString CRLF');
    AssertEqual(EscapeJsonString('tab' + #9 + 'here'), 'tab\there', 'EscapeJsonString tab');
    AssertEqual(EscapeJsonString('C:\Users\test\file.txt'), 'C:\\Users\\test\\file.txt', 'EscapeJsonString full path');
    AssertEqual(EscapeJsonString('"quoted"'), '\"quoted\"', 'EscapeJsonString leading quote');
End;

{..............................................................................}
{ 4. Response Builder Tests                                                    }
{..............................................................................}

Procedure TestResponseBuilders;
Var
    Resp : String;
Begin
    // Success response
    Resp := BuildSuccessResponse('test-id', '{"status":"ok"}');
    AssertContains(Resp, '"id":"test-id"', 'Success response has id');
    AssertContains(Resp, '"success":true', 'Success response has success');
    AssertContains(Resp, '"status":"ok"', 'Success response has data');
    AssertContains(Resp, '"error":null', 'Success response has null error');

    // Success with null data
    Resp := BuildSuccessResponse('test-id', '');
    AssertContains(Resp, '"data":null', 'Success response empty data becomes null');

    // Error response
    Resp := BuildErrorResponse('test-id', 'TEST_ERROR', 'Something failed');
    AssertContains(Resp, '"success":false', 'Error response has success false');
    AssertContains(Resp, '"TEST_ERROR"', 'Error response has error code');
    AssertContains(Resp, 'Something failed', 'Error response has message');
    AssertContains(Resp, '"data":null', 'Error response has null data');

    // Special chars in error message
    Resp := BuildErrorResponse('test-id', 'ERR', 'Path: C:\test "quoted"');
    AssertContains(Resp, '\\', 'Error response escapes backslash');
    AssertContains(Resp, '\"', 'Error response escapes quotes in message');
End;

{..............................................................................}
{ 5. Coordinate Conversion Tests                                               }
{..............................................................................}

Procedure TestCoordinates;
Begin
    AssertIntEqual(MilsToCoord(100), 1000000, 'MilsToCoord 100 mils');
    AssertIntEqual(MilsToCoord(0), 0, 'MilsToCoord zero');
    AssertIntEqual(MilsToCoord(1), 10000, 'MilsToCoord 1 mil');
    AssertIntEqual(CoordToMils(1000000), 100, 'CoordToMils 100 mils');
    AssertIntEqual(CoordToMils(10000), 1, 'CoordToMils 1 mil');
    AssertIntEqual(CoordToMils(0), 0, 'CoordToMils zero');
    // Round-trip
    AssertIntEqual(CoordToMils(MilsToCoord(42)), 42, 'MilsToCoord/CoordToMils round trip');
    AssertIntEqual(CoordToMils(MilsToCoord(500)), 500, 'Round trip 500 mils');
End;

{..............................................................................}
{ 6. String Helper Tests                                                       }
{..............................................................................}

Procedure TestStringHelpers;
Begin
    // StrToIntDef
    AssertIntEqual(StrToIntDef('42', 0), 42, 'StrToIntDef valid');
    AssertIntEqual(StrToIntDef('abc', -1), -1, 'StrToIntDef invalid');
    AssertIntEqual(StrToIntDef('', 99), 99, 'StrToIntDef empty');
    AssertIntEqual(StrToIntDef('null', 55), 55, 'StrToIntDef null string');
    AssertIntEqual(StrToIntDef('-10', 0), -10, 'StrToIntDef negative');
    AssertIntEqual(StrToIntDef('0', 5), 0, 'StrToIntDef zero');

    // BoolToJsonStr
    AssertEqual(BoolToJsonStr(True), 'true', 'BoolToJsonStr true');
    AssertEqual(BoolToJsonStr(False), 'false', 'BoolToJsonStr false');

    // StrToBool
    AssertTrue(StrToBool('true'), 'StrToBool true');
    AssertTrue(StrToBool('1'), 'StrToBool 1');
    AssertTrue(Not StrToBool('false'), 'StrToBool false');
    AssertTrue(Not StrToBool('0'), 'StrToBool 0');
    AssertTrue(Not StrToBool(''), 'StrToBool empty');

    // StrToFloatDef
    AssertTrue(Abs(StrToFloatDef('3.14', 0) - 3.14) < 0.001, 'StrToFloatDef valid');
    AssertTrue(Abs(StrToFloatDef('abc', -1.0) - (-1.0)) < 0.001, 'StrToFloatDef invalid');
    AssertTrue(Abs(StrToFloatDef('', 7.5) - 7.5) < 0.001, 'StrToFloatDef empty');
    AssertTrue(Abs(StrToFloatDef('null', 2.0) - 2.0) < 0.001, 'StrToFloatDef null string');
End;

{..............................................................................}
{ 7. Object Type Mapping Tests                                                 }
{..............................................................................}

Procedure TestObjectTypeMappings;
Begin
    // Schematic object types
    AssertTrue(ObjectTypeFromString('eNetLabel') <> -1, 'ObjectTypeFromString eNetLabel');
    AssertTrue(ObjectTypeFromString('eSchComponent') <> -1, 'ObjectTypeFromString eSchComponent');
    AssertTrue(ObjectTypeFromString('eWire') <> -1, 'ObjectTypeFromString eWire');
    AssertTrue(ObjectTypeFromString('ePin') <> -1, 'ObjectTypeFromString ePin');
    AssertTrue(ObjectTypeFromString('ePort') <> -1, 'ObjectTypeFromString ePort');
    AssertTrue(ObjectTypeFromString('ePowerObject') <> -1, 'ObjectTypeFromString ePowerObject');
    AssertTrue(ObjectTypeFromString('eParameter') <> -1, 'ObjectTypeFromString eParameter');
    AssertTrue(ObjectTypeFromString('eRectangle') <> -1, 'ObjectTypeFromString eRectangle');
    AssertTrue(ObjectTypeFromString('eLine') <> -1, 'ObjectTypeFromString eLine');
    AssertTrue(ObjectTypeFromString('eLabel') <> -1, 'ObjectTypeFromString eLabel');
    AssertTrue(ObjectTypeFromString('eSheetSymbol') <> -1, 'ObjectTypeFromString eSheetSymbol');
    AssertTrue(ObjectTypeFromString('eJunction') <> -1, 'ObjectTypeFromString eJunction');
    AssertTrue(ObjectTypeFromString('eImage') <> -1, 'ObjectTypeFromString eImage');
    AssertTrue(ObjectTypeFromString('INVALID') = -1, 'ObjectTypeFromString invalid returns -1');
    AssertTrue(ObjectTypeFromString('') = -1, 'ObjectTypeFromString empty returns -1');

    // PCB object types
    AssertTrue(ObjectTypeFromStringPCB('eTrackObject') <> -1, 'ObjectTypeFromStringPCB eTrackObject');
    AssertTrue(ObjectTypeFromStringPCB('ePadObject') <> -1, 'ObjectTypeFromStringPCB ePadObject');
    AssertTrue(ObjectTypeFromStringPCB('eViaObject') <> -1, 'ObjectTypeFromStringPCB eViaObject');
    AssertTrue(ObjectTypeFromStringPCB('eComponentObject') <> -1, 'ObjectTypeFromStringPCB eComponentObject');
    AssertTrue(ObjectTypeFromStringPCB('eArcObject') <> -1, 'ObjectTypeFromStringPCB eArcObject');
    AssertTrue(ObjectTypeFromStringPCB('eTextObject') <> -1, 'ObjectTypeFromStringPCB eTextObject');
    AssertTrue(ObjectTypeFromStringPCB('eRuleObject') <> -1, 'ObjectTypeFromStringPCB eRuleObject');
    AssertTrue(ObjectTypeFromStringPCB('INVALID') = -1, 'ObjectTypeFromStringPCB invalid returns -1');
End;

{..............................................................................}
{ 8. Layer Mapping Tests                                                       }
{..............................................................................}

Procedure TestLayerMappings;
Begin
    // String to layer and back
    AssertEqual(GetLayerString(GetLayerFromString('TopLayer')), 'TopLayer', 'Layer round-trip TopLayer');
    AssertEqual(GetLayerString(GetLayerFromString('BottomLayer')), 'BottomLayer', 'Layer round-trip BottomLayer');
    AssertEqual(GetLayerString(GetLayerFromString('TopOverlay')), 'TopOverlay', 'Layer round-trip TopOverlay');
    AssertEqual(GetLayerString(GetLayerFromString('MultiLayer')), 'MultiLayer', 'Layer round-trip MultiLayer');
    AssertEqual(GetLayerString(GetLayerFromString('KeepOutLayer')), 'KeepOutLayer', 'Layer round-trip KeepOutLayer');
    AssertEqual(GetLayerString(GetLayerFromString('Mechanical1')), 'Mechanical1', 'Layer round-trip Mechanical1');
    AssertEqual(GetLayerString(GetLayerFromString('InternalPlane1')), 'InternalPlane1', 'Layer round-trip InternalPlane1');
    AssertEqual(GetLayerString(GetLayerFromString('MidLayer1')), 'MidLayer1', 'Layer round-trip MidLayer1');

    // Unknown layer defaults to TopLayer
    AssertEqual(GetLayerString(GetLayerFromString('NonexistentLayer')), 'TopLayer', 'Unknown layer defaults to TopLayer');
End;

{..............................................................................}
{ 9. File I/O Round-Trip Tests                                                 }
{..............................................................................}

Procedure TestFileIO;
Var
    Content, ReadBack : String;
    TestPath : String;
Begin
    EnsureWorkspaceDir;
    TestPath := WorkspaceDir + 'selftest_temp.json';

    // Write and read back plain JSON
    Content := '{"id":"test","command":"application.ping","params":"{}"}';
    WriteFileContent(TestPath, Content);
    ReadBack := ReadFileContent(TestPath);
    AssertEqual(ReadBack, Content, 'File I/O round trip');

    // Clean up
    If FileExists(TestPath) Then DeleteFile(TestPath);

    // Special characters (backslashes and quotes)
    Content := '{"path":"C:\\Users\\test","msg":"say \"hello\""}';
    WriteFileContent(TestPath, Content);
    ReadBack := ReadFileContent(TestPath);
    AssertEqual(ReadBack, Content, 'File I/O with special chars');

    // Clean up
    If FileExists(TestPath) Then DeleteFile(TestPath);

    // Read non-existent file returns empty
    ReadBack := ReadFileContent(WorkspaceDir + 'does_not_exist_12345.txt');
    AssertEqual(ReadBack, '', 'ReadFileContent non-existent returns empty');
End;

{..............................................................................}
{ 10. Application Command Tests                                                }
{..............................................................................}

Procedure TestApplicationCommands;
Var
    Resp : String;
Begin
    // Ping
    Resp := App_Ping('test-ping');
    AssertValidJson(Resp, 'Ping response');
    AssertContains(Resp, '"success":true', 'Ping succeeds');
    AssertContains(Resp, '"id":"test-ping"', 'Ping has correct id');
    AssertContains(Resp, 'pong', 'Ping returns pong');

    // Version
    Resp := App_GetVersion('test-ver');
    AssertValidJson(Resp, 'Version response');
    AssertContains(Resp, '"success":true', 'Version succeeds');
    AssertContains(Resp, '"id":"test-ver"', 'Version has correct id');

    // Open documents (should always succeed, may return empty array)
    Resp := App_GetOpenDocuments('test-docs');
    AssertValidJson(Resp, 'OpenDocs response');
    AssertContains(Resp, '"success":true', 'OpenDocs succeeds');

    // Active document (should always succeed, may return empty object)
    Resp := App_GetActiveDocument('test-active');
    AssertValidJson(Resp, 'ActiveDoc response');
    AssertContains(Resp, '"success":true', 'ActiveDoc succeeds');

    // Stop server should set Running to False
    Running := True;
    Resp := HandleApplicationCommand('stop_server', '{}', 'test-stop');
    AssertContains(Resp, '"success":true', 'StopServer succeeds');
    AssertTrue(Running = False, 'StopServer sets Running to False');

    // Unknown action
    Resp := HandleApplicationCommand('nonexistent_action', '{}', 'test-unk');
    AssertContains(Resp, '"success":false', 'Unknown app action fails');
    AssertContains(Resp, 'UNKNOWN_ACTION', 'Unknown app action has correct code');
End;

{..............................................................................}
{ 11. Project Command Tests                                                    }
{..............................................................................}

Procedure TestProjectCommands;
Var
    Resp : String;
Begin
    // Get focused project (always succeeds, returns {} if no project)
    Resp := Proj_GetFocused('test-focused');
    AssertValidJson(Resp, 'GetFocused response');
    AssertContains(Resp, '"success":true', 'GetFocused succeeds');

    // Unknown action
    Resp := HandleProjectCommand('nonexistent_action', '{}', 'test-unk');
    AssertContains(Resp, '"success":false', 'Unknown project action fails');
    AssertContains(Resp, 'UNKNOWN_ACTION', 'Unknown project action has correct code');
End;

{..............................................................................}
{ 12. Library Command Tests                                                    }
{..............................................................................}

Procedure TestLibraryCommands;
Var
    Resp : String;
Begin
    // Unknown action
    Resp := HandleLibraryCommand('nonexistent_action', '{}', 'test-unk');
    AssertContains(Resp, '"success":false', 'Unknown library action fails');
    AssertContains(Resp, 'UNKNOWN_ACTION', 'Unknown library action has correct code');

    // GetComponents with no library available (should error gracefully)
    Resp := Lib_GetComponents('{}', 'test-getcomp');
    AssertValidJson(Resp, 'GetComponents no-lib response');
    // Either succeeds (if lib is open) or fails gracefully
    AssertTrue((Pos('"success":true', Resp) > 0) Or (Pos('"success":false', Resp) > 0),
        'GetComponents returns valid success or error');
End;

{..............................................................................}
{ 13. Generic Command Tests                                                    }
{..............................................................................}

Procedure TestGenericCommands;
Var
    Resp : String;
Begin
    // Unknown action
    Resp := HandleGenericCommand('nonexistent_action', '{}', 'test-unk');
    AssertContains(Resp, '"success":false', 'Unknown generic action fails');
    AssertContains(Resp, 'UNKNOWN_ACTION', 'Unknown generic action has correct code');

    // Query with invalid object type
    Resp := Gen_QueryObjects('{"object_type":"eInvalidType"}', 'test-badtype');
    AssertContains(Resp, '"success":false', 'Query invalid type fails');
    AssertContains(Resp, 'INVALID_TYPE', 'Query invalid type has correct error code');

    // Modify with missing set param
    Resp := Gen_ModifyObjects('{"object_type":"eWire","set":""}', 'test-noset');
    AssertContains(Resp, '"success":false', 'Modify without set fails');
    AssertContains(Resp, 'MISSING_PARAMS', 'Modify without set has correct code');

    // Create with invalid type
    Resp := Gen_CreateObject('{"object_type":"eInvalidType"}', 'test-badcreate');
    AssertContains(Resp, '"success":false', 'Create invalid type fails');

    // Delete with invalid type
    Resp := Gen_DeleteObjects('{"object_type":"eInvalidType"}', 'test-baddelete');
    AssertContains(Resp, '"success":false', 'Delete invalid type fails');

    // RunProcess with missing process name
    Resp := Gen_RunProcess('{"process":""}', 'test-noprocess');
    AssertContains(Resp, '"success":false', 'RunProcess without name fails');
    AssertContains(Resp, 'MISSING_PARAMS', 'RunProcess without name has correct code');

    // Zoom (should succeed even with no document -- returns success)
    Resp := Gen_Zoom('{"action":"fit"}', 'test-zoom');
    AssertValidJson(Resp, 'Zoom response');
    AssertContains(Resp, '"success":true', 'Zoom fit succeeds');

    // DeselectAll (may succeed or fail depending on open doc)
    Resp := Gen_DeselectAll('test-desel');
    AssertValidJson(Resp, 'DeselectAll response');
End;

{..............................................................................}
{ 14. Command Dispatch Tests                                                   }
{..............................................................................}

Procedure TestCommandDispatch;
Var
    Resp : String;
Begin
    // Valid command: application.ping
    Resp := ProcessCommand('application.ping', '{}', 'disp-1');
    AssertContains(Resp, '"success":true', 'Dispatch application.ping');
    AssertContains(Resp, '"id":"disp-1"', 'Dispatch preserves request id');

    // Valid command: application.get_version
    Resp := ProcessCommand('application.get_version', '{}', 'disp-2');
    AssertContains(Resp, '"success":true', 'Dispatch application.get_version');

    // Valid command: application.get_open_documents
    Resp := ProcessCommand('application.get_open_documents', '{}', 'disp-3');
    AssertContains(Resp, '"success":true', 'Dispatch application.get_open_documents');

    // Valid command: application.get_active_document
    Resp := ProcessCommand('application.get_active_document', '{}', 'disp-4');
    AssertContains(Resp, '"success":true', 'Dispatch application.get_active_document');

    // Valid command: project.get_focused
    Resp := ProcessCommand('project.get_focused', '{}', 'disp-5');
    AssertContains(Resp, '"success":true', 'Dispatch project.get_focused');

    // Unknown category
    Resp := ProcessCommand('unknown.action', '{}', 'disp-6');
    AssertContains(Resp, '"success":false', 'Dispatch unknown category fails');
    AssertContains(Resp, 'UNKNOWN_COMMAND', 'Dispatch unknown category has correct code');

    // Unknown action within valid category
    Resp := ProcessCommand('application.nonexistent', '{}', 'disp-7');
    AssertContains(Resp, '"success":false', 'Dispatch unknown action fails');

    // No dot in command
    Resp := ProcessCommand('nodot', '{}', 'disp-8');
    AssertContains(Resp, '"success":false', 'Dispatch no-dot command fails');

    // All four categories dispatch correctly (test error path for unknown action)
    Resp := ProcessCommand('library.nonexistent', '{}', 'disp-9');
    AssertContains(Resp, 'UNKNOWN_ACTION', 'Dispatch library unknown action');

    Resp := ProcessCommand('generic.nonexistent', '{}', 'disp-10');
    AssertContains(Resp, 'UNKNOWN_ACTION', 'Dispatch generic unknown action');
End;

{..............................................................................}
{ 15. Full IPC Round-Trip Test                                                 }
{..............................................................................}

Procedure TestIPCRoundTrip;
Var
    RequestPath, ResponsePath, RequestJson, ResponseJson : String;
    ProcessedOk : Boolean;
Begin
    EnsureWorkspaceDir;
    RequestPath := WorkspaceDir + REQUEST_FILE;
    ResponsePath := WorkspaceDir + RESPONSE_FILE;

    // Clean up any existing files
    If FileExists(RequestPath) Then DeleteFile(RequestPath);
    If FileExists(ResponsePath) Then DeleteFile(ResponsePath);

    // Write a request as if Python wrote it
    RequestJson := '{"id":"ipc-test-1","command":"application.ping","params":"{}"}';
    WriteFileContent(RequestPath, RequestJson);

    // Verify request file was written
    AssertTrue(FileExists(RequestPath), 'IPC request file exists after write');

    // Process it (like the polling loop would)
    ProcessedOk := ProcessSingleRequest;
    AssertTrue(ProcessedOk, 'ProcessSingleRequest returns True');

    // Request file should have been deleted
    AssertTrue(Not FileExists(RequestPath), 'IPC request file deleted after processing');

    // Read the response
    ResponseJson := ReadFileContent(ResponsePath);
    AssertNotEmpty(ResponseJson, 'IPC response not empty');
    AssertContains(ResponseJson, '"id":"ipc-test-1"', 'IPC response has correct id');
    AssertContains(ResponseJson, '"success":true', 'IPC response succeeds');
    AssertContains(ResponseJson, 'pong', 'IPC response has pong data');

    // Clean up
    If FileExists(ResponsePath) Then DeleteFile(ResponsePath);

    // Test with application.get_version
    RequestJson := '{"id":"ipc-test-2","command":"application.get_version","params":"{}"}';
    WriteFileContent(RequestPath, RequestJson);
    ProcessedOk := ProcessSingleRequest;
    AssertTrue(ProcessedOk, 'IPC version request processed');
    ResponseJson := ReadFileContent(ResponsePath);
    AssertContains(ResponseJson, '"id":"ipc-test-2"', 'IPC version response has correct id');
    AssertContains(ResponseJson, '"success":true', 'IPC version response succeeds');
    If FileExists(ResponsePath) Then DeleteFile(ResponsePath);

    // Test with an invalid command
    RequestJson := '{"id":"ipc-test-3","command":"bogus.command","params":"{}"}';
    WriteFileContent(RequestPath, RequestJson);
    ProcessedOk := ProcessSingleRequest;
    AssertTrue(ProcessedOk, 'IPC bogus command processed');
    ResponseJson := ReadFileContent(ResponsePath);
    AssertContains(ResponseJson, '"id":"ipc-test-3"', 'IPC error response has correct id');
    AssertContains(ResponseJson, '"success":false', 'IPC error response fails');
    If FileExists(ResponsePath) Then DeleteFile(ResponsePath);

    // Test with empty request file (should return False)
    WriteFileContent(RequestPath, '');
    ProcessedOk := ProcessSingleRequest;
    AssertTrue(Not ProcessedOk, 'ProcessSingleRequest returns False for empty request');

    // Test with no request file (should return False)
    If FileExists(RequestPath) Then DeleteFile(RequestPath);
    ProcessedOk := ProcessSingleRequest;
    AssertTrue(Not ProcessedOk, 'ProcessSingleRequest returns False when no file');
End;

{..............................................................................}
{ 16. Application RunProcess Parameter Parsing Tests                           }
{..............................................................................}

Procedure TestRunProcessParsing;
Var
    Resp : String;
Begin
    // Missing process_name should fail
    Resp := App_RunProcess('{"process_name":"","parameters":""}', 'test-rp1');
    AssertContains(Resp, '"success":false', 'RunProcess empty name fails');
    AssertContains(Resp, 'INVALID_PARAMETER', 'RunProcess empty name has correct code');

    // Also test the generic run_process
    Resp := Gen_RunProcess('{"process":"","params":""}', 'test-rp2');
    AssertContains(Resp, '"success":false', 'Gen RunProcess empty name fails');
    AssertContains(Resp, 'MISSING_PARAMS', 'Gen RunProcess empty name has correct code');
End;

{..............................................................................}
{ 17. Edge Case Tests                                                          }
{..............................................................................}

Procedure TestEdgeCases;
Var
    Resp : String;
Begin
    // Response builders with empty id
    Resp := BuildSuccessResponse('', '{"test":1}');
    AssertContains(Resp, '"id":""', 'Success response with empty id');

    // ExtractJsonValue with deeply nested braces
    Resp := '{"a":{"b":{"c":"deep"}}}';
    AssertTrue(Pos('"c"', ExtractJsonValue(Resp, 'a')) > 0, 'ExtractJsonValue deeply nested');

    // ExtractJsonValue with array value (not key)
    Resp := '{"items":[1,2,3],"ok":true}';
    AssertEqual(ExtractJsonValue(Resp, 'ok'), 'true', 'ExtractJsonValue after array');

    // Ensure workspace dir exists
    EnsureWorkspaceDir;
    AssertTrue(DirectoryExists(WorkspaceDir), 'WorkspaceDir exists after EnsureWorkspaceDir');
End;

{..............................................................................}
{ Main Entry Point                                                             }
{..............................................................................}

Procedure RunSelfTest;
Var
    Summary : String;
    LogPath : String;
Begin
    SelfTest_Passed := 0;
    SelfTest_Failed := 0;
    SelfTest_Log := '';

    EnsureWorkspaceDir;

    // Pure logic tests (no Altium document APIs needed)
    TestJsonParsing;
    TestJsonArrayExtraction;
    TestJsonEscaping;
    TestResponseBuilders;
    TestCoordinates;
    TestStringHelpers;
    TestObjectTypeMappings;
    TestLayerMappings;
    TestFileIO;
    TestEdgeCases;
    TestRunProcessParsing;

    // Command tests (need Altium running)
    TestApplicationCommands;
    TestProjectCommands;
    TestLibraryCommands;
    TestGenericCommands;
    TestCommandDispatch;
    TestIPCRoundTrip;

    // Build summary
    If SelfTest_Failed = 0 Then
        Summary := 'ALL TESTS PASSED: ' + IntToStr(SelfTest_Passed) + ' tests'
    Else
        Summary := 'FAILED: ' + IntToStr(SelfTest_Failed) + ' of '
            + IntToStr(SelfTest_Passed + SelfTest_Failed) + ' tests'
            + #13#10 + #13#10 + SelfTest_Log;

    // Write log to file for reference
    LogPath := WorkspaceDir + 'selftest_results.txt';
    WriteFileContent(LogPath, Summary);

    // Show result
    ShowMessage(Summary);
End;
