{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsCore.pas - the shared part of the rows commands (docs/ROWS_CONTRACT.md). }
{                                                                              }
{ rows.read_rows narrows a walk to ONE object kind inside Altium and returns only   }
{ the fields asked for; rows.write_rows takes one row per object, keyed by its id   }
{ and guarded by any of its fields, and writes them all in one edit or none.   }
{ One crossing each way, whatever the number of objects.                       }
{                                                                              }
{ This unit knows nothing of any kind. A kind (RowsPcbLib.pas ...) supplies a  }
{ field table, a getter and a setter, and drives its own walks with these      }
{ helpers. DelphiScript has no procedure values, so the walks cannot be        }
{ shared as code that calls back into the kind: what is shared is everything   }
{ around them -- the field table, the compiled conditions, the rows, the      }
{ sorted id lookup, the reply.                                                 }
{                                                                              }
{ A FIELD TABLE is 'name:type:access' entries joined by '|', names in lower   }
{ case: type s text / b boolean / l length / a angle / i integer,            }
{ access w writable / r read-only. Every table starts with id:s:r.            }
{                                                                              }
{ NUMBERS TRAVEL AS SCALED INTEGERS (2026-11-10): l = Altium's own coordinate,}
{ 1/10000 mil; a = 1/10000 degree; i = as it is. The client does             }
{ every unit conversion; this side never parses or formats a decimal (main's  }
{ locale-safe StrToFloatDef / FloatToJsonStr cost ~170 us each, measured), so }
{ no decimal separator, no locale. Integers are CANONICAL (no '+', no leading }
{ zeros, no '-0') and inside the 32-bit range, checked once (RowsIntStr), so  }
{ text equality IS numeric equality. A call's units are ignored: every     }
{ reply says units "coord".                                                   }
{..............................................................................}

{ RowsIntStr - whether S is a CANONICAL integer that fits 32 bits: digits,  }
{ an optional '-', no '+', no leading zero, not '-0'. Canonical so that     }
{ equal numbers are equal text; the range so that StrToInt cannot raise     }
{ (its error is a modal the script cannot catch).                           }
{ CHARACTERS ONLY, NO CONVERTER: MEASURED (AD 25.8.1, 2026-11-11) Altium's  }
{ StrToIntDef RAISES on a number past 32 bits ('99999999999' is not a valid }
{ integer value -- EConvertError, a modal that stopped the script), unlike  }
{ Delphi's and Free Pascal's, which return the default. So nothing here     }
{ calls a converter on text it has not checked character by character, and }
{ StrToInt / StrToIntDef are only ever handed text that passed this.       }
Function RowsIntStr(S : String) : Boolean;
Var
    D : String;
    I : Integer;
Begin
    Result := False;
    D := S;
    If Copy(D, 1, 1) = '-' Then D := Copy(D, 2, Length(D));
    If (D = '') Or (Length(D) > 10) Then Exit;
    If (Length(D) > 1) And (Copy(D, 1, 1) = '0') Then Exit;
    If (D = '0') And (Copy(S, 1, 1) = '-') Then Exit;
    For I := 1 To Length(D) Do
        If (D[I] < '0') Or (D[I] > '9') Then Exit;
    { same length: text order is number order }
    If (Length(D) = 10) And (D > '2147483647') Then Exit;
    Result := True;
End;

{ RowsLen - a length (a coordinate, or a Double worked out from them) as  }
{ the integer the wire carries. Units is 'coord' everywhere (kept so the    }
{ getters keep one signature).                                             }
Function RowsLen(Coord : Double; Units : String) : String;
Begin
    Result := IntToStr(Round(Coord));
End;

{ RowsCoord - a wire length as a coordinate; 0 for text that is not a      }
{ canonical integer (checked again here: a path that skipped the parse     }
{ must not reach StrToInt, whose error the script cannot catch).          }
Function RowsCoord(Value : String; Units : String) : Integer;
Begin
    If RowsIntStr(Trim(Value)) Then Result := StrToInt(Trim(Value)) Else Result := 0;
End;

{ RowsAngle - an angle (degrees, as Altium keeps it) as the integer the     }
{ rows wire carries: 1/10000 degree.                                       }
Function RowsAngle(Value : Double) : String;
Begin
    Result := IntToStr(Round(Value * 10000.0));
End;

{ RowsAngleDeg - a wire angle (1/10000 degree) as degrees for Altium; 0 for }
{ text that is not a canonical integer (RowsParseFlat refused it already).  }
Function RowsAngleDeg(Value : String) : Double;
Begin
    If RowsIntStr(Trim(Value)) Then Result := StrToInt(Trim(Value)) / 10000.0
    Else Result := 0;
End;

{ RowsIntOr - an integer from request text, Fallback when the text is not a }
{ canonical 32-bit integer. The ONLY way rows code turns request text into  }
{ a number: Altium's StrToIntDef raises on a number past 32 bits (see       }
{ RowsIntStr).                                                              }
Function RowsIntOr(Value : String; Fallback : Integer) : Integer;
Begin
    If RowsIntStr(Trim(Value)) Then Result := StrToInt(Trim(Value)) Else Result := Fallback;
End;

{ RowsIntOf - a canonical integer's value, 0 for anything else. }
Function RowsIntOf(Value : String) : Integer;
Begin
    If RowsIntStr(Trim(Value)) Then Result := StrToInt(Trim(Value)) Else Result := 0;
End;

{ ---- the stage profile ------------------------------------------------- }
{ A request with profile=1 gets "profile" in its reply data: milliseconds  }
{ per stage (each mark names the stage that ENDS there) and the total. Off }
{ costs one Boolean test a mark. GetTickCount counts in ~16 ms steps: a    }
{ stage is only meaningful on a call of a second or more. The state is in  }
{ Main.pas's globals (RowsProf*), set by HandleRowsCommand first thing.    }

{ RowsProfStart - the profile of this request: on when it asks profile=1. }
Procedure RowsProfStart(Params : String);
Begin
    RowsProfOn := Trim(ExtractJsonValue(Params, 'profile')) = '1';
    RowsProfText := '';
    RowsProfTick := GetTickCount;
    RowsProfStartTick := RowsProfTick;
End;

{ RowsProfMark - the stage that ends now: the milliseconds since the last  }
{ mark, under Stage. A stage name used twice adds up.                      }
Procedure RowsProfMark(Stage : String);
Var
    T : Integer;
Begin
    If Not RowsProfOn Then Exit;
    T := GetTickCount;
    RowsProfText := RowsProfText + ',"' + Stage + '":' + IntToStr(T - RowsProfTick);
    RowsProfTick := T;
End;

{ RowsProfJson - the reply data's profile member, '' when off.        }
Function RowsProfJson(Dummy : Integer) : String;
Begin
    Result := '';
    If Not RowsProfOn Then Exit;
    Result := ',"profile":{"total":' + IntToStr(GetTickCount - RowsProfStartTick)
        + RowsProfText + '}';
End;

{ RowsFieldAt - where a field's entry starts in '|' + the table, 0 when the  }
{ table has no such field. Tables are written in lower case, so only the     }
{ name is lowered: lowering the table per cell cost more than the read.      }
Function RowsFieldAt(Spec : String; Name : String) : Integer;
Begin
    Result := Pos('|' + LowerCase(Trim(Name)) + ':', '|' + Spec);
End;

{ RowsFieldType - n, s or b; '' for a field the table does not have. }
Function RowsFieldType(Spec : String; Name : String) : String;
Var
    P : Integer;
    N : String;
Begin
    Result := '';
    N := LowerCase(Trim(Name));
    P := RowsFieldAt(Spec, N);
    If P = 0 Then Exit;
    Result := Copy('|' + Spec, P + Length(N) + 2, 1);
End;

{ RowsFieldWritable - whether the table lets the field be written. }
Function RowsFieldWritable(Spec : String; Name : String) : Boolean;
Var
    P : Integer;
    N : String;
Begin
    Result := False;
    N := LowerCase(Trim(Name));
    P := RowsFieldAt(Spec, N);
    If P = 0 Then Exit;
    Result := Copy('|' + Spec, P + Length(N) + 4, 1) = 'w';
End;

{ RowsSpecNames - the field names in table order, one per line. The caller }
{ frees the list.                                                          }
Function RowsSpecNames(Spec : String) : TStringList;
Var
    Rest, Item : String;
    P : Integer;
Begin
    Result := TStringList.Create;
    Rest := Spec;
    While Rest <> '' Do
    Begin
        P := Pos('|', Rest);
        If P = 0 Then
        Begin
            Item := Rest;
            Rest := '';
        End
        Else
        Begin
            Item := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        P := Pos(':', Item);
        If P > 0 Then Item := Copy(Item, 1, P - 1);
        Item := LowerCase(Trim(Item));
        If Item <> '' Then Result.Add(Item);
    End;
End;

{ RowsSpecNameList - the field names as one comma list, for messages. }
Function RowsSpecNameList(Spec : String) : String;
Var
    Names : TStringList;
    I : Integer;
Begin
    Result := '';
    Names := RowsSpecNames(Spec);
    Try
        For I := 0 To Names.Count - 1 Do
        Begin
            If Result <> '' Then Result := Result + ', ';
            Result := Result + Names.Strings[I];
        End;
    Finally
        Names.Free;
    End;
End;

{ RowsSpecJson - a field table as a JSON array of name / type / writable. }
Function RowsSpecJson(Spec : String) : String;
Var
    Names : TStringList;
    I : Integer;
    N : String;
Begin
    Result := '';
    Names := RowsSpecNames(Spec);
    Try
        For I := 0 To Names.Count - 1 Do
        Begin
            N := Names.Strings[I];
            If Result <> '' Then Result := Result + ',';
            Result := Result + '{"name":"' + N + '","type":"' + RowsFieldType(Spec, N)
                + '","writable":' + BoolToJsonStr(RowsFieldWritable(Spec, N)) + '}';
        End;
    Finally
        Names.Free;
    End;
    Result := '[' + Result + ']';
End;

{ RowsParseFields - the fields a read returns: the comma list asked for, or  }
{ every field of the table; id always first and once. Problem names an      }
{ unknown field. The caller frees the list.                                  }
Function RowsParseFields(Spec : String; Wanted : String; Var Problem : String) : TStringList;
Var
    Rest, Item : String;
    P : Integer;
    All : TStringList;
Begin
    Problem := '';
    Result := TStringList.Create;
    Result.Add('id');
    If Trim(Wanted) = '' Then
    Begin
        All := RowsSpecNames(Spec);
        Try
            For P := 0 To All.Count - 1 Do
                If All.Strings[P] <> 'id' Then Result.Add(All.Strings[P]);
        Finally
            All.Free;
        End;
        Exit;
    End;
    Rest := Wanted;
    While Rest <> '' Do
    Begin
        P := Pos(',', Rest);
        If P = 0 Then
        Begin
            Item := Rest;
            Rest := '';
        End
        Else
        Begin
            Item := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        Item := LowerCase(Trim(Item));
        If Item = '' Then Continue;
        If RowsFieldType(Spec, Item) = '' Then
        Begin
            Problem := 'unknown field "' + Item + '"; this kind has: '
                + RowsSpecNameList(Spec);
            Exit;
        End;
        If Result.IndexOf(Item) < 0 Then Result.Add(Item);
    End;
End;

{ RowsFieldTypes - the type of each field in Fields, aligned, looked up once }
{ per call rather than once per cell. The caller frees the list.            }
Function RowsFieldTypes(Spec : String; Fields : TStringList) : TStringList;
Var
    I : Integer;
Begin
    Result := TStringList.Create;
    For I := 0 To Fields.Count - 1 Do
        Result.Add(RowsFieldType(Spec, Fields.Strings[I]));
End;

{ RowsPointListOk - a point list as type v carries it: two or more points }
{ x,y joined by ";", every number a canonical integer.                    }
Function RowsPointListOk(Text : String) : Boolean;
Var
    Rest, Pt : String;
    I, N : Integer;
Begin
    Result := False;
    Rest := Trim(Text);
    N := 0;
    While Rest <> '' Do
    Begin
        I := Pos(';', Rest);
        If I > 0 Then
        Begin
            Pt := Trim(Copy(Rest, 1, I - 1));
            Rest := Trim(Copy(Rest, I + 1, Length(Rest)));
        End
        Else
        Begin
            Pt := Trim(Rest);
            Rest := '';
        End;
        I := Pos(',', Pt);
        If I < 1 Then Exit;
        If Not RowsIntStr(Trim(Copy(Pt, 1, I - 1))) Then Exit;
        If Not RowsIntStr(Trim(Copy(Pt, I + 1, Length(Pt)))) Then Exit;
        Inc(N);
    End;
    Result := N >= 2;
End;

{ RowsTypeWord - what an integer type counts, for messages. }
Function RowsTypeWord(T : String) : String;
Begin
    If T = 'l' Then Result := 'a length in 1/10000 mil'
    Else If T = 'a' Then Result := 'an angle in 1/10000 degree'
    Else If T = 'v' Then Result := 'points x,y;x,y in 1/10000 mil'
    Else Result := 'an integer';
End;

{ RowsTypeProblem - RowsValueProblem for a field whose type letter T is     }
{ known already.                                                            }
Function RowsTypeProblem(T : String; Name : String; Value : String) : String;
Var
    V : String;
Begin
    Result := '';
    V := Trim(Value);
    If ((T = 'l') Or (T = 'a') Or (T = 'i')) And (Not RowsIntStr(V)) Then
        Result := Name + ' takes a whole number (' + RowsTypeWord(T)
            + '; no decimals, no leading zeros), not "' + Value + '"'
    Else If (T = 'b') And (LowerCase(V) <> 'true') And (LowerCase(V) <> 'false') Then
        Result := Name + ' takes true or false, not "' + Value + '"'
    Else If (T = 'v') And (Not RowsPointListOk(V)) Then
        Result := Name + ' takes two or more points, x,y;x,y (' + RowsTypeWord(T)
            + ', whole numbers), not "' + Value + '"';
End;

{ RowsValueProblem - whether Value suits the field's type: a canonical    }
{ integer for l / a / i, true / false for b. '' when it does.              }
Function RowsValueProblem(Spec : String; Name : String; Value : String) : String;
Begin
    Result := RowsTypeProblem(RowsFieldType(Spec, Name), Name, Value);
End;

{ RowsCompileWhere - the conditions of a where, parsed once: one entry per   }
{ condition, field TAB operator TAB value TAB field type. Joined by a      }
{ backtick (`) and all must hold: '|' and ';' occur in real values (Altium's }
{ rule strings, pin functions), and rows take no escapes. Problem names the }
{ first one that cannot be understood. The caller frees the list.          }
Function RowsCompileWhere(Spec : String; Where : String; Var Problem : String) : TStringList;
Var
    Rest, Cond, Name, Op, Value : String;
    P : Integer;
Begin
    Problem := '';
    Result := TStringList.Create;
    Rest := Where;
    While Rest <> '' Do
    Begin
        P := Pos('`', Rest);
        If P = 0 Then
        Begin
            Cond := Rest;
            Rest := '';
        End
        Else
        Begin
            Cond := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        If Trim(Cond) = '' Then Continue;
        P := FilterOpPos(Cond);
        Op := FilterOpText(Cond);
        If (P = 0) Or (Op = '') Then
        Begin
            Problem := '"' + Cond + '" is not field op value (op = != < > <= >= ~ !~)';
            Exit;
        End;
        Name := LowerCase(Trim(Copy(Cond, 1, P - 1)));
        Value := Trim(Copy(Cond, P + Length(Op), Length(Cond)));
        If RowsFieldType(Spec, Name) = '' Then
        Begin
            Problem := 'unknown field "' + Name + '" in where; this kind has: '
                + RowsSpecNameList(Spec);
            Exit;
        End;
        If (Op <> '~') And (Op <> '!~') Then
        Begin
            Problem := RowsValueProblem(Spec, Name, Value);
            If Problem <> '' Then
            Begin
                Problem := 'where: ' + Problem;
                Exit;
            End;
        End;
        Result.Add(Name + Chr(9) + Op + Chr(9) + Value + Chr(9) + RowsFieldType(Spec, Name));
    End;
End;

{ RowsPart - the Nth (0-based) tab-separated part of a compiled entry. }
Function RowsPart(Entry : String; N : Integer) : String;
Var
    Rest : String;
    I, P : Integer;
Begin
    Rest := Entry;
    For I := 1 To N Do
    Begin
        P := Pos(Chr(9), Rest);
        If P = 0 Then
        Begin
            Result := '';
            Exit;
        End;
        Rest := Copy(Rest, P + 1, Length(Rest));
    End;
    P := Pos(Chr(9), Rest);
    If P = 0 Then Result := Rest Else Result := Copy(Rest, 1, P - 1);
End;

{ RowsColumn - part N of every compiled condition, as its own list, so a     }
{ walk reads Conds' field / op / value / type by index instead of splitting  }
{ each condition again for every object. The caller frees the list.         }
Function RowsColumn(Conds : TStringList; N : Integer) : TStringList;
Var
    I : Integer;
Begin
    Result := TStringList.Create;
    For I := 0 To Conds.Count - 1 Do
        Result.Add(RowsPart(Conds.Strings[I], N) + Chr(1));
End;

{ RowsCol - an entry of a RowsColumn list (stored with a trailing Chr(1) so  }
{ an empty value is never an empty Add).                                     }
Function RowsCol(Col : TStringList; I : Integer) : String;
Var
    S : String;
Begin
    S := Col.Strings[I];
    Result := Copy(S, 1, Length(S) - 1);
End;

{ RowsCondHolds - whether a field's value Have stands in relation Op to Want. }
{ Integers compare exactly; text compares ignore case; an unreadable value   }
{ ('') matches nothing.                                                      }
Function RowsCondHolds(FieldType : String; Have : String; Op : String; Want : String) : Boolean;
Var
    Eq : Boolean;
Begin
    Result := False;
    If Have = '' Then Exit;
    If (FieldType = 'l') Or (FieldType = 'a') Or (FieldType = 'i') Then
    Begin
        { canonical integers on both sides (RowsIntStr at the parse, IntToStr }
        { from the getters): = is text equality, no conversion at all.       }
        If (Op = '~') Or (Op = '!~') Then
        Begin
            Result := FilterOpHolds(Op, Have, Want, False);
            Exit;
        End;
        Want := Trim(Want);
        If Op = '=' Then Result := Have = Want
        Else If Op = '!=' Then Result := Have <> Want
        Else If RowsIntStr(Have) And RowsIntStr(Want) Then
        Begin
            If Op = '<' Then Result := StrToInt(Have) < StrToInt(Want)
            Else If Op = '>' Then Result := StrToInt(Have) > StrToInt(Want)
            Else If Op = '<=' Then Result := StrToInt(Have) <= StrToInt(Want)
            Else If Op = '>=' Then Result := StrToInt(Have) >= StrToInt(Want);
        End;
        Exit;
    End;
    Eq := UpperCase(Have) = UpperCase(Trim(Want));
    Result := FilterOpHolds(Op, Have, Want, Eq);
End;

{ RowsCell - one value of a reply row: numbers and booleans bare, text   }
{ quoted, an unreadable number or boolean null.                          }
Function RowsCell(FieldType : String; Value : String) : String;
Begin
    If (FieldType = 's') Or (FieldType = 'v') Then
        Result := '"' + EscapeJsonString(Value) + '"'
    Else If Value = '' Then
        Result := 'null'
    Else If FieldType = 'b' Then
    Begin
        If LowerCase(Value) = 'true' Then Result := 'true' Else Result := 'false';
    End
    Else
        Result := Value;
End;

{ RowsCellCache - the cell cache of a reader with N fields (RowsCellCached): }
{ two entries per column, the last raw value and its cell, started at Chr(1) }
{ which no value equals. The caller frees it.                               }
Function RowsCellCache(N : Integer) : TStringList;
Var
    I : Integer;
Begin
    Result := TStringList.Create;
    For I := 1 To 2 * N Do Result.Add(Chr(1));
End;

{ RowsCellCached - RowsCell for column I of a reader's reply, reusing the  }
{ last cell of that column when the value repeats (the footprint name, its  }
{ description, the layer, the shape ... repeat object after object). Main's }
{ EscapeJsonString scans every character in an interpreted loop: building }
{ the reply rows of a 1000-pad read cost as much as reading the fields     }
{ (stage profile, 2026-11-11). Cache = RowsCellCache(Fields.Count).        }
Function RowsCellCached(FieldType : String; Value : String; I : Integer;
    Cache : TStringList) : String;
Begin
    If FieldType <> 's' Then
    Begin
        Result := RowsCell(FieldType, Value);
        Exit;
    End;
    If Cache.Strings[2 * I] = Value Then
    Begin
        Result := Cache.Strings[2 * I + 1];
        Exit;
    End;
    Result := RowsCell(FieldType, Value);
    Cache.Strings[2 * I] := Value;
    Cache.Strings[2 * I + 1] := Result;
End;

{ RowsFieldsJson - the reply's field list. }
Function RowsFieldsJson(Fields : TStringList) : String;
Var
    I : Integer;
Begin
    Result := '';
    For I := 0 To Fields.Count - 1 Do
    Begin
        If Result <> '' Then Result := Result + ',';
        Result := Result + '"' + Fields.Strings[I] + '"';
    End;
    Result := '[' + Result + ']';
End;

{ RowsJoin - the list's lines joined by commas, in one allocation (TStrings }
{ .Text): a reply of thousands of rows is not grown one row at a time.      }
Function RowsJoin(Lines : TStringList) : String;
Begin
    Result := '';
    If Lines.Count = 0 Then Exit;
    Result := StringReplace(Lines.Text, Chr(13) + Chr(10), ',', -1);
    If Copy(Result, Length(Result), 1) = ',' Then
        Result := Copy(Result, 1, Length(Result) - 1);
End;

{ RowsReadReply - the data of a rows.read_rows reply. }
Function RowsReadReply(Doc : String; Kind : String; Units : String; Fields : TStringList;
    Lines : TStringList; Offset : Integer; More : Boolean; Scanned : Integer;
    Note : String) : String;
Var
    Joined : String;
Begin
    RowsProfMark('walk');
    Joined := RowsJoin(Lines);
    RowsProfMark('reply');
    Result := '{"doc":"' + Doc + '","kind":"' + Kind + '"'
        + ',"units":"coord"'
        + ',"fields":' + RowsFieldsJson(Fields)
        + ',"rows":[' + Joined + ']'
        + ',"count":' + IntToStr(Lines.Count)
        + ',"offset":' + IntToStr(Offset)
        + ',"more":' + BoolToJsonStr(More)
        + ',"scanned":' + IntToStr(Scanned)
        + ',"note":"' + EscapeJsonString(Note) + '"'
        + RowsProfJson(0) + '}';
End;

{ RowsSplit - Text cut at every Sep, in order, by Altium's own string      }
{ calls: the separator becomes a line break and the list takes the text in }
{ ONE assignment. For the whole rows text (thousands of rows): main's      }
{ NextBatchOp copies the remaining text once per row. NOT for one short   }
{ row: MEASURED (rows.probe_cost, AD 25.8, 2026-11-10) 0.36 ms a call, the }
{ character loop in RowsPairs is cheaper there. A line break inside a     }
{ piece is swapped out first and put back, so a multiline value survives. }
{ Empty pieces are kept (the caller decides). The caller frees the list.  }
Function RowsSplit(Text : String; Sep : String) : TStringList;
Var
    T : String;
    I : Integer;
    Breaks : Boolean;
Begin
    Result := TStringList.Create;
    If Text = '' Then Exit;
    Breaks := (Pos(Chr(13), Text) > 0) Or (Pos(Chr(10), Text) > 0);
    T := Text;
    If Breaks Then
    Begin
        T := StringReplace(T, Chr(13), Chr(16), -1);
        T := StringReplace(T, Chr(10), Chr(17), -1);
    End;
    { A trailing separator would otherwise lose the empty piece after it. }
    Result.Text := StringReplace(T, Sep, Chr(13) + Chr(10), -1) + Chr(13) + Chr(10);
    If Breaks Then
        For I := 0 To Result.Count - 1 Do
            Result.Strings[I] := StringReplace(StringReplace(Result.Strings[I],
                Chr(16), Chr(13), -1), Chr(17), Chr(10), -1);
End;

{ RowsJsonValue - a top-level text value of the request, as ExtractJsonValue }
{ gives it, but a value with no backslash is copied, not unescaped: main's  }
{ unescape walks the value a character at a time (~1.8 s for 56 kB of rows, }
{ measured 2026-11-10). Uses main's cached key index (JsonIndexOf); anything }
{ else (nested, a non-text value) goes to ExtractJsonValue.                 }
Function RowsJsonValue(Json : String; Key : String) : String;
Var
    Keys, Span, Raw : String;
    P, S, E : Integer;
Begin
    Result := '';
    If (Key = '') Or (Pos('"' + Key + '"', Json) = 0) Then Exit;
    Keys := JsonIndexOf(Json);
    P := Pos('|' + Key + '=', Keys);
    If P = 0 Then
    Begin
        Result := ExtractJsonValue(Json, Key);
        Exit;
    End;
    Span := Copy(Keys, P + Length(Key) + 2, Length(Keys));
    Span := Copy(Span, 1, Pos('|', Span) - 1);
    S := StrToIntDef(Copy(Span, 1, Pos(',', Span) - 1), 0);
    E := StrToIntDef(Copy(Span, Pos(',', Span) + 1, Length(Span)), 0);
    If (S <= 0) Or (E < S) Or (Copy(Json, S, 1) <> '"') Then
    Begin
        Result := ExtractJsonValue(Json, Key);
        Exit;
    End;
    Raw := Copy(Json, S + 1, E - S - 1);
    If Pos('\', Raw) = 0 Then Result := Raw
    Else Result := UnescapeJsonString(Raw);
End;

{ RowsRowsText - the rows text of a write: from rows_file when the request }
{ names one, else the inline rows value. rows_file is a FILE NAME in the    }
{ bridge workspace (the client writes it beside the request and deletes it }
{ after the reply), read with main's LoadTextFile: the rows never pass     }
{ through main's JSON index / unescape, which walk text a character at a   }
{ time. A path, or a file that cannot be read, comes back as Chr(1) + the  }
{ reason, which RowsParseRows refuses as BAD_ROWS.                          }
Function RowsRowsText(Params : String) : String;
Var
    Name : String;
    SL : TStringList;
Begin
    Name := Trim(RowsJsonValue(Params, 'rows_file'));
    If Name = '' Then
    Begin
        Result := RowsJsonValue(Params, 'rows');
        Exit;
    End;
    If (Pos('\', Name) > 0) Or (Pos('/', Name) > 0) Or (Pos(':', Name) > 0)
        Or (Pos('..', Name) > 0) Then
    Begin
        Result := Chr(1) + 'rows_file is a file name in the bridge workspace, not a path: "'
            + Name + '"';
        Exit;
    End;
    SL := TStringList.Create;
    Try
        If LoadTextFile(SL, WorkspaceDir + Name) Then
            Result := SL.Text
        Else
            Result := Chr(1) + 'rows_file "' + Name
                + '" could not be read from the bridge workspace';
    Finally
        SL.Free;
    End;
End;

{ RowsPairs - one row's fields as key TAB value lines, in row order. A     }
{ row's fields are joined by a backtick (`), never ';': ';' and '|' occur   }
{ in real values (pin functions, Altium's rule strings), and rows take no   }
{ escapes. Only the first '=' of a field splits it. The caller frees the    }
{ list.                                                                      }
Function RowsPairs(Row : String) : TStringList;
Var
    Rest, Field : String;
    P, Q : Integer;
Begin
    { A character loop, not RowsSplit: MEASURED (rows.probe_cost, AD 25.8,  }
    { 2026-11-10) on a three-field row 0.70 ms here against 1.05 ms native  }
    { (the string list and StringReplace cost more than a short loop).     }
    Result := TStringList.Create;
    Rest := Row;
    While Rest <> '' Do
    Begin
        P := Pos('`', Rest);
        If P = 0 Then
        Begin
            Field := Rest;
            Rest := '';
        End
        Else
        Begin
            Field := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        If Trim(Field) = '' Then Continue;
        Q := Pos('=', Field);
        If Q = 0 Then
            Result.Add(LowerCase(Trim(Field)) + Chr(9))
        Else
            Result.Add(LowerCase(Trim(Copy(Field, 1, Q - 1))) + Chr(9)
                + Trim(Copy(Field, Q + 1, Length(Field))));
    End;
End;

{ RowsRowField - the value a row gives the field Name (no '?'), '' when it }
{ has none. Main's GetBatchField splits on ';', which a rows value may hold. }
Function RowsRowField(Row : String; Name : String) : String;
Var
    Pairs : TStringList;
    I : Integer;
Begin
    Result := '';
    Pairs := RowsPairs(Row);
    Try
        For I := 0 To Pairs.Count - 1 Do
            If RowsPart(Pairs.Strings[I], 0) = LowerCase(Name) Then
                Result := RowsPart(Pairs.Strings[I], 1);
    Finally
        Pairs.Free;
    End;
End;

{ RowsRowGuard - the value a row's guard on Name asks for, '' when it has  }
{ none (a guard against an empty text cannot be written, by design).       }
Function RowsRowGuard(Row : String; Name : String) : String;
Var
    Pairs : TStringList;
    I : Integer;
Begin
    Result := '';
    Pairs := RowsPairs(Row);
    Try
        For I := 0 To Pairs.Count - 1 Do
            If RowsPart(Pairs.Strings[I], 0) = '?' + LowerCase(Name) Then
                Result := RowsPart(Pairs.Strings[I], 1);
    Finally
        Pairs.Free;
    End;
End;

{ ---- the flat row table ------------------------------------------------ }
{ A write's rows are split ONCE. Every field of every row goes into Keys /  }
{ Vals / Types (the same index in each), in row order: Keys in lower case,  }
{ a guard with its '?', the id as 'id'; Vals trimmed; Types the field's     }
{ type letter. A row's fields are Keys[S] .. Keys[E - 1], S and E kept as   }
{ text in RS / RE beside its id. MEASURED (rows.probe_cost, AD 25.8,        }
{ 2026-11-10): one split of a three-field row is 0.70 ms and a pad write   }
{ split each row ~12 times; reading Keys by index costs one statement.      }

{ RowsFlatAdd - one row's fields appended to Keys / Vals (Types gets ''),   }
{ split on the backtick by a character loop (see RowsPairs). Returns the    }
{ index of the first field it added.                                        }
Function RowsFlatAdd(Row : String; Keys : TStringList; Vals : TStringList;
    Types : TStringList) : Integer;
Var
    Rest, Field, Key, Value : String;
    P, Q : Integer;
Begin
    Result := Keys.Count;
    Rest := Row;
    While Rest <> '' Do
    Begin
        P := Pos('`', Rest);
        If P = 0 Then
        Begin
            Field := Rest;
            Rest := '';
        End
        Else
        Begin
            Field := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        If Trim(Field) = '' Then Continue;
        Q := Pos('=', Field);
        If Q = 0 Then
        Begin
            Key := LowerCase(Trim(Field));
            Value := '';
        End
        Else
        Begin
            Key := LowerCase(Trim(Copy(Field, 1, Q - 1)));
            Value := Trim(Copy(Field, Q + 1, Length(Field)));
        End;
        Keys.Add(Key);
        Vals.Add(Value);
        Types.Add(Value);
    End;
End;

{ RowsFlatValue - the value fields S .. E - 1 give Key (lower case, with  }
{ its '?' for a guard); the last one when given twice, '' when none.     }
Function RowsFlatValue(Keys : TStringList; Vals : TStringList; S : Integer;
    E : Integer; Key : String) : String;
Var
    F : Integer;
Begin
    Result := '';
    For F := S To E - 1 Do
        If Keys.Strings[F] = Key Then Result := Vals.Strings[F];
End;

{ RowsFlatWrites - whether field F is a write (not the id, not a guard). }
Function RowsFlatWrites(Keys : TStringList; F : Integer) : Boolean;
Var
    K : String;
Begin
    K := Keys.Strings[F];
    Result := (K <> 'id') And (Copy(K, 1, 1) <> '?');
End;

{ RowsFlatKept - RowsFlatValue over the fields Keep marks: Keep '' = every }
{ field, else one character per field from S ('1' kept, '0' not).         }
Function RowsFlatKept(Keys : TStringList; Vals : TStringList; S : Integer;
    E : Integer; Keep : String; Key : String) : String;
Var
    F : Integer;
Begin
    Result := '';
    For F := S To E - 1 Do
        If Keys.Strings[F] = Key Then
            If (Keep = '') Or (Copy(Keep, F - S + 1, 1) = '1') Then
                Result := Vals.Strings[F];
End;


{ RowsParseFlat - every row of a write checked before Altium is touched,   }
{ each split once into the flat table (Keys / Vals / Types). Ids comes back }
{ SORTED (binary IndexOf) and Texts / RS / RE aligned with it: row Ids[k]  }
{ is Texts[k], its fields Keys[RS[k]] .. Keys[RE[k] - 1]. Required names   }
{ the container guard every row must carry ('footprint' for pcblib ...; ''  }
{ for none). Returns '' when all rows are fine, else the problem with its   }
{ error code in Code. Every list comes in new and empty.                    }
Function RowsParseFlat(Spec : String; RowsStr : String; Required : String;
    Ids : TStringList; Texts : TStringList; RS : TStringList; RE : TStringList;
    Keys : TStringList; Vals : TStringList; Types : TStringList;
    Var Code : String) : String;
Var
    Row, Id, Key, Name, Value, T, Info : String;
    Order, Plain, OS, OE, AllRows, InfoNames, InfoVals : TStringList;
    I, K, R, S, E, F, Writes, N : Integer;
Begin
    Result := '';
    Code := '';
    Ids.Sorted := True;
    Order := TStringList.Create;
    Plain := TStringList.Create;
    OS := TStringList.Create;
    OE := TStringList.Create;
    { Each field name's type and access, looked up in the table ONCE: a     }
    { write's rows repeat the same few names (stage profile, 2026-11-11).   }
    InfoNames := TStringList.Create;
    InfoVals := TStringList.Create;
    If Copy(RowsStr, 1, 1) = Chr(1) Then
    Begin
        { RowsRowsText could not hand over the rows (see there). }
        Code := 'BAD_ROWS';
        Result := Copy(RowsStr, 2, Length(RowsStr)) + '; nothing was changed';
        AllRows := TStringList.Create;
    End
    Else
        AllRows := RowsSplit(RowsStr, '~~');
    Try
        If Result <> '' Then Exit;
        For R := 0 To AllRows.Count - 1 Do
        Begin
            Row := AllRows.Strings[R];
            If Row = '' Then Continue;
            S := RowsFlatAdd(Row, Keys, Vals, Types);
            E := Keys.Count;
            Id := RowsFlatValue(Keys, Vals, S, E, 'id');
            If (Id = '') Or (Ids.IndexOf(Id) >= 0) Then
            Begin
                Code := 'BAD_ROWS';
                Result := 'every row needs an id of its own (missing or repeated: "'
                    + Id + '"); nothing was changed';
                Exit;
            End;
            Ids.Add(Id);
            Order.Add(Id);
            Plain.Add(Row);
            OS.Add(IntToStr(S));
            OE.Add(IntToStr(E));
            Writes := 0;
            For F := S To E - 1 Do
            Begin
                Key := Keys.Strings[F];
                Value := Vals.Strings[F];
                If Key = 'id' Then
                Begin
                    Types.Strings[F] := 's';
                    Continue;
                End;
                If Copy(Key, 1, 1) = '?' Then Name := Copy(Key, 2, Length(Key))
                Else Name := Key;
                N := InfoNames.IndexOf(Name);
                If N < 0 Then
                Begin
                    Info := RowsFieldType(Spec, Name);
                    If RowsFieldWritable(Spec, Name) Then Info := Info + 'w'
                    Else Info := Info + 'r';
                    InfoNames.Add(Name);
                    InfoVals.Add(Info);
                    N := InfoNames.Count - 1;
                End;
                Info := InfoVals.Strings[N];
                T := Copy(Info, 1, 1);
                If T = 'w' Then T := '';     { not in the table: 'w' / 'r' alone }
                If T = 'r' Then T := '';
                Types.Strings[F] := T;
                If T = '' Then
                Begin
                    Code := 'BAD_FIELD';
                    Result := 'row ' + Id + ': unknown field "' + Name
                        + '"; this kind has: ' + RowsSpecNameList(Spec)
                        + '; nothing was changed';
                    Exit;
                End;
                If Copy(Key, 1, 1) <> '?' Then
                Begin
                    If Copy(Info, Length(Info), 1) <> 'w' Then
                    Begin
                        Code := 'READ_ONLY_FIELD';
                        Result := 'row ' + Id + ': ' + Name + ' is read-only (use ?'
                            + Name + '= to guard on it); nothing was changed';
                        Exit;
                    End;
                    Inc(Writes);
                End;
                If (Value <> '') Or (T <> 's') Then
                Begin
                    Result := RowsTypeProblem(T, Name, Value);
                    If Result <> '' Then
                    Begin
                        Code := 'BAD_VALUE';
                        Result := 'row ' + Id + ': ' + Result + '; nothing was changed';
                        Exit;
                    End;
                End;
            End;
            If Writes = 0 Then
            Begin
                Code := 'BAD_ROWS';
                Result := 'row ' + Id + ' writes nothing; nothing was changed';
                Exit;
            End;
            If (Required <> '')
                And (RowsFlatValue(Keys, Vals, S, E, '?' + LowerCase(Required)) = '') Then
            Begin
                Code := 'BAD_ROWS';
                Result := 'row ' + Id + ' needs ?' + Required + '= (where the object is); '
                    + 'nothing was changed';
                Exit;
            End;
        End;
        If Ids.Count = 0 Then
        Begin
            Code := 'MISSING_PARAMS';
            Result := 'rows is empty';
            Exit;
        End;
        { A placeholder per id (an empty Add is refused by the linter), each }
        { then replaced by its row.                                          }
        For I := 0 To Ids.Count - 1 Do
        Begin
            Texts.Add('-');
            RS.Add('0');
            RE.Add('0');
        End;
        For I := 0 To Order.Count - 1 Do
        Begin
            K := Ids.IndexOf(Order.Strings[I]);
            Texts.Strings[K] := Plain.Strings[I];
            RS.Strings[K] := OS.Strings[I];
            RE.Strings[K] := OE.Strings[I];
        End;
    Finally
        Order.Free;
        Plain.Free;
        OS.Free;
        OE.Free;
        InfoNames.Free;
        InfoVals.Free;
        AllRows.Free;
    End;
End;

{ RowsParseRows - RowsParseFlat for a writer that reads the row text      }
{ itself: the same checks, the flat table thrown away.                     }
Function RowsParseRows(Spec : String; RowsStr : String; Required : String;
    Ids : TStringList; Texts : TStringList; Var Code : String) : String;
Var
    RS, RE, Keys, Vals, Types : TStringList;
Begin
    RS := TStringList.Create;
    RE := TStringList.Create;
    Keys := TStringList.Create;
    Vals := TStringList.Create;
    Types := TStringList.Create;
    Try
        Result := RowsParseFlat(Spec, RowsStr, Required, Ids, Texts, RS, RE,
            Keys, Vals, Types, Code);
    Finally
        RS.Free;  RE.Free;  Keys.Free;  Vals.Free;  Types.Free;
    End;
End;

{ ---- the table file ---------------------------------------------------- }
{ rows.py hands a write over as the FLAT TABLE already split (table_file, a }
{ file name in the bridge workspace): the script loads it with native list }
{ calls and loops only once per row, never per character or per field.   }
{ RowsParseFlat cost 2.5 s of a 1000-pad write (stage profile, 2026-11-11). }
{ The file is lines (CRLF), sections divided by a line holding Chr(1):     }
{   'ROWSTABLE 1', N rows, M cells, B ('1' = values had line breaks,      }
{     swapped for Chr(16) / Chr(17))                                       }
{   the distinct keys of the cells (id, ?name guards, names written)       }
{   the N ids, in row order                                                }
{   the N row starts, the N row ends (cell indexes, end exclusive)         }
{   the M keys, then the M values, row after row, each row's id first      }
{ rows.py has checked every row (rows_rules.py, the only way in). Kept    }
{ here as a backstop: every key known to the kind, every written key      }
{ writable, the counts, the row ranges one after another, an id once.     }
{ Values are not checked again: every number is read through RowsIntStr  }
{ where it is used (RowsCoord, RowsIntOr ...), so no text reaches StrToInt }
{ unchecked.                                                              }

{ RowsTableCut - the next section of T (up to the Chr(1) line) as list    }
{ text, T keeping the rest; the last section is the whole of T.           }
Function RowsTableCut(Var T : String) : String;
Var
    M : String;
    P : Integer;
Begin
    M := Chr(13) + Chr(10) + Chr(1) + Chr(13) + Chr(10);
    P := Pos(M, T);
    If P = 0 Then
    Begin
        Result := T;
        T := '';
        Exit;
    End;
    { the line break that ends the section's last line stays with it, so an }
    { empty last line is kept                                               }
    Result := Copy(T, 1, P + 1);
    T := Copy(T, P + 5, Length(T));
End;

{ RowsDigitLines - whether Text is lines of 1 to 9 digits (native calls  }
{ only: StrToInt may then take each line).                                }
Function RowsDigitLines(Text : String) : Boolean;
Var
    X : String;
    I : Integer;
Begin
    Result := False;
    If (Text = '') Or (Pos(Chr(13) + Chr(10) + Chr(13) + Chr(10), Chr(13) + Chr(10) + Text) > 0) Then
        Exit;
    X := Text;
    For I := 0 To 9 Do
        X := StringReplace(X, IntToStr(I), '', -1);
    If StringReplace(X, Chr(13) + Chr(10), '', -1) <> '' Then Exit;
    { no line of ten digits or more }
    X := Chr(13) + Chr(10) + Text;
    For I := 0 To 9 Do
        X := StringReplace(X, IntToStr(I), '0', -1);
    Result := Pos('0000000000', X) = 0;
End;

{ RowsTableTake - the table file's text (see above) into the flat table,  }
{ as RowsParseFlat leaves it: Ids sorted, Texts / RS / RE aligned with it  }
{ (Texts holds the ids: no writer reads the row text). '' when fine, else }
{ the problem with its error code in Code. Every list comes in new.       }
Function RowsTableTake(Spec : String; Text : String; Ids : TStringList; Texts : TStringList;
    RS : TStringList; RE : TStringList; Keys : TStringList; Vals : TStringList;
    Types : TStringList; Var Code : String) : String;
Var
    T, Head, Sec, KT, Key, Name, FT, CRLF : String;
    H, Fields, Order, S0, E0 : TStringList;
    N, M, I, K : Integer;
    Last : String;
Begin
    Result := '';
    Code := 'BAD_ROWS';
    CRLF := Chr(13) + Chr(10);
    T := Text;
    If Copy(T, Length(T) - 1, 2) <> CRLF Then T := T + CRLF;
    H := TStringList.Create;
    Fields := TStringList.Create;
    Order := TStringList.Create;
    S0 := TStringList.Create;
    E0 := TStringList.Create;
    Try
        Head := RowsTableCut(T);
        H.Text := Head;
        If (H.Count <> 4) Or (H.Strings[0] <> 'ROWSTABLE 1')
            Or (Not RowsDigitLines(H.Strings[1] + CRLF + H.Strings[2])) Then
        Begin
            Result := 'the rows table file is not one rows.py wrote; nothing was changed';
            Exit;
        End;
        N := StrToInt(H.Strings[1]);
        M := StrToInt(H.Strings[2]);
        Fields.Text := RowsTableCut(T);
        Order.Text := RowsTableCut(T);
        Sec := RowsTableCut(T);
        If Not RowsDigitLines(Sec) Then
        Begin
            Result := 'the rows table''s row starts are not whole numbers; nothing was changed';
            Exit;
        End;
        S0.Text := Sec;
        Sec := RowsTableCut(T);
        If Not RowsDigitLines(Sec) Then
        Begin
            Result := 'the rows table''s row ends are not whole numbers; nothing was changed';
            Exit;
        End;
        E0.Text := Sec;
        Sec := RowsTableCut(T);
        Keys.Text := Sec;
        Vals.Text := T;
        If (N < 1) Or (Order.Count <> N) Or (S0.Count <> N) Or (E0.Count <> N)
            Or (Keys.Count <> M) Or (Vals.Count <> M) Then
        Begin
            Result := 'the rows table does not hold the ' + IntToStr(N) + ' rows and '
                + IntToStr(M) + ' cells it says; nothing was changed';
            Exit;
        End;
        If H.Strings[3] = '1' Then
            For I := 0 To M - 1 Do
                Vals.Strings[I] := StringReplace(StringReplace(Vals.Strings[I],
                    Chr(16), Chr(13), -1), Chr(17), Chr(10), -1);

        { Each distinct key once against the kind's table; the cell types by }
        { replacing each key's line with its type letter (native calls). Two }
        { equal keys are never neighbours: a row starts with its id and holds }
        { a key once (rows.py).                                              }
        KT := CRLF + Sec;
        For I := 0 To Fields.Count - 1 Do
        Begin
            Key := Fields.Strings[I];
            If Key = 'id' Then FT := 's'
            Else
            Begin
                If Copy(Key, 1, 1) = '?' Then Name := Copy(Key, 2, Length(Key))
                Else Name := Key;
                FT := RowsFieldType(Spec, Name);
                If FT = '' Then
                Begin
                    Code := 'BAD_FIELD';
                    Result := 'unknown field "' + Name + '"; this kind has: '
                        + RowsSpecNameList(Spec) + '; nothing was changed';
                    Exit;
                End;
                If (Copy(Key, 1, 1) <> '?') And (Not RowsFieldWritable(Spec, Name)) Then
                Begin
                    Code := 'READ_ONLY_FIELD';
                    Result := Name + ' is read-only (use ?' + Name
                        + '= to guard on it); nothing was changed';
                    Exit;
                End;
            End;
            KT := StringReplace(KT, CRLF + Key + CRLF, CRLF + Chr(2) + FT + CRLF, -1);
        End;
        { every line replaced: Chr(2) + a letter, M times, then the last break; }
        { a line left (a key not in the list) keeps its break and its text.    }
        Sec := StringReplace(KT, CRLF + Chr(2), Chr(2), -1);
        If Length(Sec) <> 2 * M + 2 Then
        Begin
            Result := 'the rows table has keys its key list does not name; nothing was changed';
            Exit;
        End;
        Types.Text := StringReplace(Copy(KT, 3, Length(KT)), Chr(2), '', -1);

        { Ids sorted (binary IndexOf); a repeated id is dropped by the sorted }
        { list, so the count tells.                                          }
        Ids.Sorted := True;
        Ids.Text := Order.Text;
        If Ids.Count <> N Then
        Begin
            Result := 'every row needs an id of its own (one is repeated); nothing was changed';
            Exit;
        End;
        Texts.Text := Ids.Text;
        RS.Text := Ids.Text;
        RE.Text := Ids.Text;
        Last := '0';
        For I := 0 To N - 1 Do
        Begin
            { the rows one after another, from cell 0 to cell M }
            If S0.Strings[I] <> Last Then
            Begin
                Result := 'the rows table''s rows are not one after another; nothing was changed';
                Exit;
            End;
            Last := E0.Strings[I];
            K := Ids.IndexOf(Order.Strings[I]);
            RS.Strings[K] := S0.Strings[I];
            RE.Strings[K] := Last;
        End;
        If Last <> IntToStr(M) Then
        Begin
            Result := 'the rows table''s rows do not end at its last cell; nothing was changed';
            Exit;
        End;
        Code := '';
    Finally
        H.Free;
        Fields.Free;
        Order.Free;
        S0.Free;
        E0.Free;
    End;
End;

{ RowsUndoOff - True when the request asks undo=false. Only the library  }
{ pad / via writers honour it; the dispatcher refuses it elsewhere.     }
Function RowsUndoOff(Params : String) : Boolean;
Begin
    Result := LowerCase(Trim(ExtractJsonValue(Params, 'undo'))) = 'false';
End;

{ RowsTakeRows - a write's rows into the flat table: from table_file when   }
{ the request names one (rows.py), else the rows text (rows_file or rows)   }
{ through RowsParseFlat. Marks the profile stages load and parse.          }
Function RowsTakeRows(Spec : String; Params : String; Required : String;
    Ids : TStringList; Texts : TStringList; RS : TStringList; RE : TStringList;
    Keys : TStringList; Vals : TStringList; Types : TStringList;
    Var Code : String) : String;
Var
    Name, Text : String;
    SL : TStringList;
Begin
    Name := Trim(RowsJsonValue(Params, 'table_file'));
    If Name = '' Then
    Begin
        Text := RowsRowsText(Params);
        RowsProfMark('load');
        Result := RowsParseFlat(Spec, Text, Required, Ids, Texts, RS, RE,
            Keys, Vals, Types, Code);
        RowsProfMark('parse');
        Exit;
    End;
    Code := 'BAD_ROWS';
    If (Pos('\', Name) > 0) Or (Pos('/', Name) > 0) Or (Pos(':', Name) > 0)
        Or (Pos('..', Name) > 0) Then
    Begin
        Result := 'table_file is a file name in the bridge workspace, not a path: "'
            + Name + '"; nothing was changed';
        Exit;
    End;
    SL := TStringList.Create;
    Try
        If Not LoadTextFile(SL, WorkspaceDir + Name) Then
        Begin
            Result := 'table_file "' + Name + '" could not be read from the bridge '
                + 'workspace; nothing was changed';
            Exit;
        End;
        Text := SL.Text;
    Finally
        SL.Free;
    End;
    RowsProfMark('load');
    Result := RowsTableTake(Spec, Text, Ids, Texts, RS, RE, Keys, Vals, Types, Code);
    RowsProfMark('parse');
End;

{ RowsWriteReply - the data of a rows.write_rows reply. Results holds one JSON   }
{ object per written row.                                                   }
Function RowsWriteReply(Doc : String; Kind : String; Rows : Integer; Written : Integer;
    Failed : Integer; Repeats : Integer; Results : TStringList; Note : String) : String;
Var
    Joined : String;
Begin
    RowsProfMark('write');
    Joined := RowsJoin(Results);
    RowsProfMark('reply');
    Result := '{"doc":"' + Doc + '","kind":"' + Kind + '"'
        + ',"rows":' + IntToStr(Rows)
        + ',"written":' + IntToStr(Written)
        + ',"failed":' + IntToStr(Failed)
        + ',"repeats":' + IntToStr(Repeats)
        + ',"results":[' + Joined + ']'
        + ',"note":"' + EscapeJsonString(Note) + '"'
        + RowsProfJson(0) + '}';
End;

{ RowsWriteReplySkip - the same, for a writer that leaves alone a row that  }
{ would change nothing: Written = rows written, Unchanged = rows whose     }
{ every field already held its value (not written, still read back).      }
Function RowsWriteReplySkip(Doc : String; Kind : String; Rows : Integer; Written : Integer;
    Unchanged : Integer; Failed : Integer; Repeats : Integer; Results : TStringList;
    Note : String) : String;
Var
    Joined : String;
Begin
    RowsProfMark('write');
    Joined := RowsJoin(Results);
    RowsProfMark('reply');
    Result := '{"doc":"' + Doc + '","kind":"' + Kind + '"'
        + ',"rows":' + IntToStr(Rows)
        + ',"written":' + IntToStr(Written)
        + ',"unchanged":' + IntToStr(Unchanged)
        + ',"failed":' + IntToStr(Failed)
        + ',"repeats":' + IntToStr(Repeats)
        + ',"results":[' + Joined + ']'
        + ',"note":"' + EscapeJsonString(Note) + '"'
        + RowsProfJson(0) + '}';
End;

{ RowsOpenPcbLib - the PcbLib to work on: library_path, else the focused     }
{ document; opened (focused) when it is not the focused one, as the library  }
{ writers do. Nil with Problem set when there is none.                       }
Function RowsOpenPcbLib(LibPath : String; Var UsedPath : String; Var Problem : String) : IPCB_Library;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    FocusedPath : String;
Begin
    Result := Nil;
    Problem := '';
    UsedPath := LibPath;
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Problem := 'No workspace';
        Exit;
    End;
    FocusedPath := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then Try FocusedPath := CanonicalPathValue('path', Doc.DM_FullPath); Except End;
    If UsedPath = '' Then UsedPath := FocusedPath;
    If UsedPath = '' Then
    Begin
        Problem := 'No library is active and library_path was not supplied';
        Exit;
    End;
    If (FocusedPath = '') Or (UpperCase(FocusedPath) <> UpperCase(UsedPath)) Then
    Begin
        ResetParameters;
        AddStringParameter('ObjectKind', 'Document');
        AddStringParameter('FileName', UsedPath);
        RunProcess('WorkspaceManager:OpenObject');
    End;
    Result := PCBServer.GetCurrentPCBLibrary;
    If Result = Nil Then Problem := 'No PCB library open at ' + UsedPath;
End;

{ RowsLayerUserName - the name the layer stack gives a layer (a mechanical   }
{ layer's user name, "Top Courtyard"), '' when it cannot be read.            }
Function RowsLayerUserName(Board : IPCB_Board; Lyr : TLayer) : String;
Var
    Stack : IPCB_LayerStack_V7;
    LayerObj : IPCB_LayerObject_V7;
Begin
    Result := '';
    If Board = Nil Then Exit;
    Try
        Stack := Board.LayerStack_V7;
        If Stack <> Nil Then
        Begin
            LayerObj := Stack.LayerObject_V7[Lyr];
            If LayerObj <> Nil Then Result := LayerObj.Name;
        End;
    Except
        Result := '';
    End;
End;

{ RowsArcSweep - degrees counter-clockwise from the start angle to the end   }
{ angle, in (0, 360]: equal angles are a full circle, as Altium draws them.  }
Function RowsArcSweep(StartAngle : Double; EndAngle : Double) : Double;
Var
    S : Double;
Begin
    S := EndAngle - StartAngle;
    While S <= 0 Do S := S + 360.0;
    While S > 360.0 Do S := S - 360.0;
    Result := S;
End;

{ RowsFocusedBoard - the FOCUSED PcbDoc, never another open board: the kind  }
{ is asked of the DM layer first (touching PCBServer with no PCB focused is  }
{ a modal), and the board found must be that document. Nil with Problem set }
{ when no board is focused. Shared by every doc=pcb kind.                   }
Function RowsFocusedBoard(Var Path : String; Var Problem : String) : IPCB_Board;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    Kind, Have : String;
Begin
    Result := Nil;
    Problem := '';
    Path := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Problem := 'No workspace';
        Exit;
    End;
    Kind := '';
    Doc := Workspace.DM_FocusedDocument;
    If Doc <> Nil Then
    Begin
        Try Kind := UpperCase(Doc.DM_DocumentKind); Except End;
        Try Path := Doc.DM_FullPath; Except End;
    End;
    If Kind <> 'PCB' Then
    Begin
        Problem := 'The focused document is not a PcbDoc; rows doc=pcb works on the '
            + 'focused board only (focus it and retry)';
        Exit;
    End;
    { With a PcbDoc in front GetPCBBoardAnywhere takes its fast path, the    }
    { current board; the path is compared anyway, so another board is never }
    { answered for this one.                                                 }
    Result := GetPCBBoardAnywhere(0);
    If Result = Nil Then
    Begin
        Problem := 'No PCB board is focused';
        Exit;
    End;
    Have := '';
    Try Have := Result.FileName; Except End;
    If UpperCase(StringReplace(Have, '/', '\', -1))
        <> UpperCase(StringReplace(Path, '/', '\', -1)) Then
    Begin
        Problem := 'The board found (' + Have + ') is not the focused document ('
            + Path + '); nothing was done';
        Result := Nil;
    End;
End;

{ UnitStamp_RowsCore - which build of RowsCore.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_RowsCore(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
