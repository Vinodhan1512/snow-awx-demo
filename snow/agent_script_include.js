var AWXDemoAgent = Class.create();
AWXDemoAgent.prototype = {
    // Backend for the Virtual Agent topics. It creates the same tickets as the web form, so the
    // "AWX Demo - launch" Business Rule fires and AWX writes the results back to the ticket.
    // Every method returns an object with ok (true/false) and message (text for the bot to show).
    // Keep property names unquoted only if they are not JS reserved words (see snow_config.py).

    initialize: function() {},

    // Use case 1: incident with read-only network diagnostics
    createDiagnosticsIncident: function(device, intf) {
        device = this._cleanDevice(device);
        intf = this._cleanIntf(intf, false);
        if (!intf) {
            return { ok: false, message: 'I did not recognise that interface name. Examples: GigabitEthernet1, Loopback200.' };
        }
        var gr = new GlideRecord('incident');
        gr.initialize();
        gr.caller_id = gs.getUserID();
        gr.short_description = 'Interface ' + intf + ' reported down on ' + device + ' (raised via Virtual Agent)';
        gr.u_automation_type = 'incident_diagnostics';
        gr.u_device = device;
        gr.u_interface = intf;
        return this._result('incident', gr.insert());
    },

    // Use case 2: change that enables (or shuts down) a loopback interface, with pre/post checks
    createInterfaceChange: function(device, intf, description, shutdown) {
        device = this._cleanDevice(device);
        intf = this._cleanIntf(intf, true);
        if (!intf) {
            return { ok: false, message: 'Only loopback interfaces can be changed from chat. Examples: Loopback200, or Loopback200-209 for up to 20 at once.' };
        }
        description = String(description || '').replace(/[\r\n]+/g, ' ').substring(0, 80);
        var shut = String(shutdown) == 'true';
        var gr = new GlideRecord('change_request');
        gr.initialize();
        gr.type = 'normal';
        gr.requested_by = gs.getUserID();
        gr.short_description = (shut ? 'Shut down ' : 'Enable ') + intf + ' on ' + device + ' (raised via Virtual Agent)';
        gr.u_automation_type = 'cisco_interface_change';
        gr.u_device = device;
        gr.u_interface = intf;
        gr.u_int_description = description;
        gr.u_int_shutdown = shut;
        return this._result('change_request', gr.insert());
    },

    // Status of a demo ticket (INC... or CHG...), with the latest work note as plain text
    getStatus: function(number) {
        number = String(number || '').trim().toUpperCase();
        var table = number.indexOf('CHG') === 0 ? 'change_request' : 'incident';
        var gr = new GlideRecord(table);
        if (!number || !gr.get('number', number)) {
            return { ok: false, message: 'I could not find ticket ' + number + '.' };
        }
        var status = gr.getValue('u_automation_status') || '';
        var label = status ? gr.getDisplayValue('u_automation_status') : 'Not started';
        var msg = number + ': ' + label + '.';
        var note = this._lastNote(gr.getUniqueValue());
        if (note && status && status != 'running') {
            msg += '\n' + note;
        } else if (status == 'running') {
            msg += ' AWX is still working on it. Ask me again in about a minute.';
        }
        return { ok: true, number: number, status: status, done: !!status && status != 'queued' && status != 'running',
                 awx_job: gr.getValue('u_awx_job') || '', link: this._link(table, gr.getUniqueValue()), message: msg };
    },

    _result: function(table, sysId) {
        if (!sysId) {
            return { ok: false, message: 'The ticket could not be created.' };
        }
        // The launch Business Rule has already run inside insert(), so the AWX job link is on the record now
        var gr = new GlideRecord(table);
        gr.get(sysId);
        var job = gr.getValue('u_awx_job') || '';
        var status = gr.getValue('u_automation_status') || '';
        var msg = 'Created ' + gr.getValue('number') + '. ';
        if (status == 'running') {
            msg += 'Automation started in AWX. Results will be written to the ticket in about a minute.';
        } else if (status == 'failed') {
            msg += 'The automation could not be started - see the work notes on the ticket.';
        } else {
            msg += 'Automation status: ' + (status || 'not started') + '.';
        }
        return { ok: true, number: gr.getValue('number'), sys_id: sysId, status: status, awx_job: job,
                 link: this._link(table, sysId), message: msg };
    },

    _cleanDevice: function(device) {
        device = String(device || '').trim();
        return /^[A-Za-z0-9._-]{1,60}$/.test(device) ? device : 'demo-iosxe';
    },

    _cleanIntf: function(intf, loopbackOnly) {
        intf = String(intf || '').replace(/\s+/g, '');
        var m = /^(lo|loopback)(\d{1,4})$/i.exec(intf);
        if (m) return 'Loopback' + m[2];
        if (loopbackOnly) {
            // bulk: a range of up to 20 loopbacks, e.g. Loopback200-209 or lo200-lo209
            m = /^(lo|loopback)(\d{1,4})-(lo|loopback)?(\d{1,4})$/i.exec(intf);
            if (m && parseInt(m[4], 10) >= parseInt(m[2], 10) && parseInt(m[4], 10) - parseInt(m[2], 10) < 20) {
                return 'Loopback' + m[2] + '-' + m[4];
            }
            return '';
        }
        m = /^(gi|gigabitethernet)(\d{1,2}(\/\d{1,2}){0,2})$/i.exec(intf);
        if (m) return 'GigabitEthernet' + m[2];
        return '';
    },

    _lastNote: function(sysId) {
        var j = new GlideRecord('sys_journal_field');
        j.addQuery('element_id', sysId);
        j.addQuery('element', 'work_notes');
        j.orderByDesc('sys_created_on');
        j.setLimit(1);
        j.query();
        if (!j.next()) return '';
        var t = String(j.getValue('value') || '')
            .replace(/\[\/?code\]/g, ' ').replace(/<br\s*\/?>|<\/li>|<\/tr>|<\/h3>/gi, '\n').replace(/<[^>]+>/g, ' ')
            .replace(/&mdash;/g, '-').replace(/&middot;/g, '|').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&#39;/g, "'").replace(/&quot;/g, '"')
            .replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n');
        var cut = t.search(/\n\s*show /);          // drop raw command output; it is in the attachment
        if (cut > 0) t = t.substring(0, cut);
        return t.trim().substring(0, 600);
    },

    _link: function(table, sysId) {
        return gs.getProperty('glide.servlet.uri') + table + '.do?sys_id=' + sysId + '&sysparm_view=';
    },

    type: 'AWXDemoAgent'
};
