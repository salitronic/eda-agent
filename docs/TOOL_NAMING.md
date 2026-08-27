# Tool naming convention

Every MCP tool follows `ns_verb_object`:

1. **Namespace** (required, first token): one of
   `app` · `proj` · `sch` · `pcb` · `lib` · `obj` · `sim` · `audit` · `design`.
   The namespace says which subsystem the tool acts on. `obj` is the generic
   object layer (CRUD primitives, selection, view, fonts) that is not specific
   to schematic or PCB.
2. **Verb** (second token): a present-tense action. Prefer the shared set
   `get` (one/known) · `list` (enumerate) · `create` · `add` · `set` ·
   `update` · `delete` · `remove` · `place` · `move` · `copy` · `run` ·
   `export` · `import` · `open` · `close` · `save` · `attach` · `link` ·
   `find` · `check` · `calc` · `plan` · `sync` · `render`. Domain-idiomatic
   verbs that are already clear (`flip`, `align`, `snap`, `repour`, `tune`,
   `panelize`, `normalize`, `increment`, `stub`, `bevel`, `layout`) are kept.
3. **Object** (remaining tokens): the noun. The namespace already carries the
   subsystem, so redundant domain words are dropped from the object
   (`get_altium_status` -> `app_get_status`, `create_project` -> `proj_create`).

Bulk tools keep a plural object (`pcb_place_tracks`); a read that has a
single-item sibling keeps the `_many` suffix (`proj_get_component_info_many`).

The IPC command strings passed to the bridge (e.g. `"pcb.move_component"`) are
independent of tool names and are NOT changed by this convention.

## Unchanged namespaces

All `pcb_*`, `lib_*`, `audit_*`, and `design_*` tools already satisfy the
convention and are left as-is. Well-formed `sch_*` tools are also kept.

## Rename map

### application -> app_

| old | new |
|---|---|
| get_altium_status | app_get_status |
| attach_to_altium | app_attach |
| save_all | app_save_all |
| detach_from_altium | app_detach |
| ping_altium | app_ping |
| get_server_report | app_get_report |
| create_document | app_create_document |
| get_open_documents | app_list_documents |
| diag_workspace | app_diag_workspace |
| get_active_document | app_get_active_document |
| set_active_document | app_set_active_document |
| get_altium_version | app_get_version |
| get_preferences | app_get_preferences |
| execute_menu | app_run_menu |
| set_intent | app_set_intent |
| get_clipboard_text | app_get_clipboard |

### project -> proj_

| old | new |
|---|---|
| create_project | proj_create |
| open_project | proj_open |
| save_project | proj_save |
| close_project | proj_close |
| get_project_documents | proj_list_documents |
| add_document_to_project | proj_add_document |
| remove_document_from_project | proj_remove_document |
| get_project_parameters | proj_get_parameters |
| set_project_parameter | proj_set_parameter |
| project_push_params_to_sheets | proj_push_parameters |
| get_nets | proj_get_nets |
| export_netlist | proj_export_netlist |
| compile_project | proj_compile |
| load_project_sheets | proj_load_sheets |
| get_bom | proj_get_bom |
| get_component_info | proj_get_component_info |
| get_component_info_many | proj_get_component_info_many |
| export_pdf | proj_export_pdf |
| cross_probe | proj_cross_probe |
| get_design_stats | proj_get_stats |
| get_board_info | proj_get_board_info |
| annotate | proj_annotate |
| generate_output | proj_run_output |
| get_focused_project | proj_get_focused |
| export_step | proj_export_step |
| export_dxf | proj_export_dxf |
| export_image | proj_export_image |
| get_outjob_containers | proj_list_outjob_containers |
| run_outjob | proj_run_outjob |
| run_outjob_all | proj_run_outjob_all |
| generate_fab_package | proj_generate_fab_package |
| get_variants | proj_list_variants |
| get_active_variant | proj_get_active_variant |
| set_active_variant | proj_set_active_variant |
| create_variant | proj_create_variant |
| get_open_projects | proj_list_open |
| get_messages | proj_get_messages |
| find_component | proj_find_component |
| get_connectivity | proj_get_connectivity |
| get_connectivity_many | proj_get_connectivity_many |
| force_recompile | proj_force_recompile |
| get_compile_freshness | proj_get_compile_freshness |
| import_document | proj_import_document |
| get_project_path | proj_get_path |
| set_document_parameter | proj_set_document_parameter |
| compare_sch_pcb | proj_compare_sch_pcb |
| update_pcb | proj_sync_pcb |
| update_schematic | proj_sync_schematic |
| get_design_differences | proj_get_differences |
| lock_designator | proj_lock_designator |
| get_project_options | proj_get_options |

### generic -> obj_ (generic primitives, selection, view, fonts)

| old | new |
|---|---|
| query_objects | obj_query |
| modify_objects | obj_modify |
| create_object | obj_create |
| delete_objects | obj_delete |
| batch_modify | obj_batch_modify |
| batch_create | obj_batch_create |
| batch_delete | obj_batch_delete |
| copy_objects | obj_copy |
| get_object_count | obj_count |
| select_objects | obj_select |
| deselect_all | obj_deselect_all |
| zoom | obj_zoom |
| switch_view | obj_switch_view |
| refresh_document | obj_refresh_document |
| set_grid | obj_set_grid |
| highlight_net | obj_highlight_net |
| clear_highlights | obj_clear_highlights |
| crossref_net | obj_crossref_net |
| get_font_spec | obj_get_font_spec |
| get_font_id | obj_get_font_id |
| get_document_info | obj_get_document_info |
| generic_run_process | obj_run_process |

### generic -> proj_ (project-level: compile, ERC, sheets, components)

| old | new |
|---|---|
| run_erc | proj_run_erc |
| get_erc_violations | proj_get_erc_violations |
| add_sheet | proj_add_sheet |
| delete_sheet | proj_delete_sheet |
| get_unconnected_pins | proj_get_unconnected_pins |
| replace_component | proj_replace_component |

### generic -> sch_ (schematic edit / place / query)

| old | new |
|---|---|
| set_sch_components_parameters | sch_set_components_parameters |
| stub_unconnected_pins | sch_stub_pins |
| place_rectangle | sch_place_rectangle |
| place_line | sch_place_line |
| place_note | sch_place_note |
| place_sheet_symbol | sch_place_sheet_symbol |
| place_sheet_entry | sch_place_sheet_entry |
| place_bus_entry | sch_place_bus_entry |
| place_bus | sch_place_bus |
| place_net_label | sch_place_net_label |
| place_port | sch_place_port |
| place_power_port | sch_place_power_port |
| place_no_erc | sch_place_no_erc |
| place_junction | sch_place_junction |
| place_image | sch_place_image |
| place_wires | sch_place_wires |
| place_sch_components_from_library | sch_place_components |
| get_sheet_parameters | sch_get_sheet_parameters |

Already-compliant `sch_*` tools (kept): sch_set_sheet_size, sch_set_units,
sch_add_directive, sch_get_directives, sch_get_constraint_groups,
sch_place_harness_connector, sch_place_cross_sheet_connector,
sch_place_text_frame, sch_generate_toc, sch_set_net_tie,
sch_increment_designators, sch_toggle_pin_visibility, sch_set_component_part_id,
sch_set_component_unique_id, sch_replicate_component, sch_place_probe,
sch_add_datafile_link.

### sim -> sim_

| old | new |
|---|---|
| sch_get_simulation_readiness | sim_get_readiness |
| sch_attach_spice_model | sim_attach_model |
| sch_attach_spice_primitives | sim_attach_primitives |
| sim_run | sim_run (unchanged) |

### render and review

| old | new |
|---|---|
| sch_render_svg | sch_render_svg (unchanged) |
| pcb_render_svg | pcb_render_svg (unchanged) |
| design_visual_review | design_visual_review (unchanged) |
| export_bom_html | proj_export_bom_html |
| design_review_snapshot | design_review_snapshot (unchanged) |
| design_lint_report | design_lint_report (unchanged) |
| datasheet_checklist | design_datasheet_checklist |
