(function executeRule(current, previous) {
    // Closes the ticket once the AWX playbook has written its final result (u_automation_status).
    //   change_request: success -> Closed (successful), rolled_back -> Closed (unsuccessful),
    //                   rejected -> Canceled, failed -> stays open for an engineer
    //   incident:       diagnosed -> Resolved, action_required -> In Progress (stays open)
    var table = current.getTableName();
    var status = current.getValue('u_automation_status');
    var gr = new GlideRecord(table);
    if (!gr.get(current.getUniqueValue())) return;
    var job = current.getValue('u_awx_job') || '';
    var by = ' by the AWX automation' + (job ? ' (' + job + ')' : '') + '.';

    if (table == 'change_request') {
        if (status == 'success') {
            gr.state = 3;
            gr.close_code = 'successful';
            gr.close_notes = 'Implemented and validated' + by + ' All post-checks passed; evidence attached.';
        } else if (status == 'rolled_back') {
            gr.state = 3;
            gr.close_code = 'unsuccessful';
            gr.close_notes = 'Post-check failed; the change was rolled back automatically' + by;
        } else if (status == 'rejected') {
            gr.state = 4;
            gr.close_code = 'unsuccessful';
            gr.close_notes = 'Rejected by the policy guardrail before any change was made' + by;
        } else {
            return;
        }
        // Demo shortcut: a Normal change cannot jump from New to Closed through its state model, so the state
        // engine is bypassed here. In production the automation runs in Implement, after CAB approval.
        gr.setWorkflow(false);
        gr.update();
        gs.info('[AWX Demo] ' + current.getValue('number') + ' closed: ' + status);
    } else if (table == 'incident') {
        if (status == 'diagnosed') {
            gr.state = 6;
            gr.close_code = 'Resolved – Fixed Without Action';
            gr.close_notes = 'Automated diagnostics found no fault requiring action' + by +
                ' Findings and command outputs are in the work notes and attachment.';
            gr.resolved_by = gs.getUserID();
            gr.resolved_at = new GlideDateTime();
        } else if (status == 'action_required') {
            gr.state = 2;
            gr.work_notes = 'Automated diagnostics found a fault that needs an engineer - incident left open.';
        } else {
            return;
        }
        gr.update();
        gs.info('[AWX Demo] ' + current.getValue('number') + ' -> ' + status);
    }
})(current, previous);
