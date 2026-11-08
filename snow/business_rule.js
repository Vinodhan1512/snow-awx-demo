(function executeRule(current, previous /*null when async*/) {
    // Launches the AWX job template mapped to u_automation_type, passing ticket fields as extra_vars.
    // The playbook writes results back to this same record (work notes, status, attachments).
    var table = current.getTableName();
    var sysId = current.getUniqueValue();
    var type = current.getValue('u_automation_type');
    if (!type) return;

    function str(f) { var v = current.getValue(f); return v ? String(v) : ''; }
    function bool(f) { var v = String(current.getValue(f)); return v == '1' || v == 'true'; }

    var rec = new GlideRecord(table);
    if (!rec.get(sysId)) return;

    try {
        var awxUrl = String(gs.getProperty('awxdemo.awx_url', '')).replace(/\/+$/, '');
        var token = gs.getProperty('awxdemo.awx_token', '');
        var templates = JSON.parse(gs.getProperty('awxdemo.templates', '{}'));
        var tid = templates[type];
        if (!awxUrl || !token || !tid) {
            rec.u_automation_status = 'failed';
            rec.work_notes = 'Automation not launched: missing AWX URL/token or no template mapped for "' + type + '".';
            rec.update();
            return;
        }

        var vars = {
            snow_table: table,
            snow_sys_id: sysId,
            snow_number: str('number'),
            automation_type: type,
            requested_by: current.getDisplayValue('opened_by'),
            target_device: str('u_device'),
            interface: str('u_interface')
        };
        if (table == 'change_request') {
            vars.int_description = str('u_int_description');
            vars.int_shutdown = bool('u_int_shutdown');
            vars.sg_id = str('u_sg_id');
            vars.sg_action = str('u_sg_action') || 'add';
            vars.sg_protocol = str('u_sg_protocol') || 'tcp';
            vars.sg_port = str('u_sg_port');
            vars.sg_cidr = str('u_sg_cidr');
            vars.simulate_failure = bool('u_simulate_failure');
        }

        var r = new sn_ws.RESTMessageV2();
        r.setEndpoint(awxUrl + '/api/v2/job_templates/' + tid + '/launch/');
        r.setHttpMethod('POST');
        r.setRequestHeader('Authorization', 'Bearer ' + token);
        r.setRequestHeader('Content-Type', 'application/json');
        r.setRequestHeader('Accept', 'application/json');
        r.setHttpTimeout(30000);
        r.setRequestBody(JSON.stringify({ extra_vars: vars }));
        var resp = r.execute();
        var code = resp.getStatusCode();
        var body = resp.getBody() || '';

        if (code == 201) {
            var job = JSON.parse(body).job;
            var link = awxUrl + '/#/jobs/playbook/' + job + '/output';
            rec.u_awx_job = link;
            rec.u_automation_status = 'running';
            rec.work_notes = '[code]<b>Automation started</b> &mdash; AWX job #' + job + ' (' + type + ')<br/>' +
                '<a href="' + link + '" target="_blank">Open live job output in AWX</a>[/code]';
        } else {
            rec.u_automation_status = 'failed';
            rec.work_notes = 'AWX launch failed: HTTP ' + code + ' ' + (resp.getErrorMessage() || '') + '\n' + body.substring(0, 800);
        }
        rec.update();
    } catch (e) {
        rec.u_automation_status = 'failed';
        rec.work_notes = 'AWX launch error: ' + e;
        rec.update();
    }
})(current, previous);
