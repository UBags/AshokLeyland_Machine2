// Query page of AshokLeylandWebService: calls the service it was loaded from (relative URLs), so it works from any PC.
function download(text, filename, type) {
    const url = window.URL.createObjectURL(new Blob([text], {type: type}));
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    window.URL.revokeObjectURL(url); document.body.removeChild(a);
}

function localDate() {   // today's date on this PC, YYYY-MM-DD (toISOString would give the UTC date)
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function show(text, count) {
    document.getElementById('result').value = text;
    document.getElementById('recordCount').textContent = `(${count} records)`;
}

function query(method, format, params) {
    params.db_name = document.getElementById('dbName').value;
    params.aborts = document.getElementById('aborts').value;     // include / only / exclude / emergency / manual
    params.result = document.getElementById('result').value;     // all / ok / notok
    params.bypassed = document.getElementById('bypassed').value; // include / only / exclude
    fetch(`api/${method}/${format}/`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(params)
    })
    .then(response => response.text().then(text => ({ok: response.ok, text: text})))
    .then(({ok, text}) => {
        const stamp = localDate();
        if (!ok) {
            let message = text;
            try { message = JSON.parse(text).error || text; } catch (e) {}
            show(message.startsWith('Error') ? message : `Error: ${message}`, 0);
            return;
        }
        if (format === 'json') {
            const rows = JSON.parse(text).data || [];
            if (rows.length === 0) { show('No records found for the selected criteria', 0); return; }
            const pretty = JSON.stringify(rows, null, 2);
            show(pretty, rows.length);
            download(pretty, `${method}${suffix(params)}_${stamp}.json`, 'application/json');
        } else {
            const count = Math.max(text.trim().split('\n').length - 1, 0);
            if (count === 0) { show('No records found for the selected criteria', 0); return; }
            show(text, count);
            download(text, `${method}${suffix(params)}_${stamp}.csv`, 'text/csv');
        }
    })
    .catch(error => show(`Error: ${error}`, 0));
}

function suffix(params) {   // the filters in the file name, when not the defaults
    return (params.aborts && params.aborts !== 'include' ? `_aborts-${params.aborts}` : '') +
           (params.result && params.result !== 'all' ? `_result-${params.result}` : '') +
           (params.bypassed && params.bypassed !== 'include' ? `_bypassed-${params.bypassed}` : '');
}

function dates(startId, endId) {
    const start = document.getElementById(startId).value, end = document.getElementById(endId).value;
    if (!start || !end) { alert('Please select both dates.'); return null; }
    if (end < start) { alert('The end date is before the start date.'); return null; }
    return {startDate: start, endDate: end};
}

function validateAndQuery(method, format, selectId = null) {
    const params = {};
    if (selectId) {
        const model = document.getElementById(selectId).value;
        if (!model) { alert('Please select a model.'); return; }
        params.modelName = model;
    }
    if (method === 'by_model_name_and_date') {
        const d = dates('startDate', 'endDate'); if (!d) return; Object.assign(params, d);
    } else if (method === 'by_date_limits') {
        const d = dates('startDate2', 'endDate2'); if (!d) return; Object.assign(params, d);
    }
    query(method, format, params);
}

const MODEL_SELECTS = [['modelName1', 'by_model_name'], ['modelName2', 'by_model_name_and_date'],
                       ['modelName3', 'today_by_model']];

function loadModels() {   // the model lists of the chosen database
    fetch('api/get_unique_model_names/json/', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({db_name: document.getElementById('dbName').value})
    })
    .then(r => r.json())
    .then(d => {
        const names = d.model_names || [];
        MODEL_SELECTS.forEach(([id, prefix]) => {
            const select = document.getElementById(id), keep = select.value;
            select.innerHTML = '<option value="">Select model</option>' +
                names.map(n => `<option value="${n.replace(/"/g, '&quot;')}">${n.replace(/</g, '&lt;')}</option>`).join('');
            if (names.includes(keep)) select.value = keep;
            document.getElementById(`${prefix}_csv_btn`).disabled = names.length === 0;
            document.getElementById(`${prefix}_json_btn`).disabled = names.length === 0;
        });
        if (d.error) show(`Error: ${d.error}`, 0);
    })
    .catch(error => show(`Error: ${error}`, 0));
}

window.onload = function () {
    const today = localDate();
    ['startDate', 'endDate', 'startDate2', 'endDate2'].forEach(id => { document.getElementById(id).value = today; });
    document.getElementById('dbName').addEventListener('change', loadModels);
    loadModels();
};
