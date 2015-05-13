var CompoundDatatypesTable = function($table, is_user_admin) {
    permissions.PermissionsTable.call(this, $table, is_user_admin);
    this.list_url = "api/compounddatatypes/";
    this.registerColumn("Scheme", "representation");
}
CompoundDatatypesTable.prototype = Object.create(
        permissions.PermissionsTable.prototype);

CompoundDatatypesTable.prototype.buildTable = function(rows) {
    rows.sort(function(a, b) {
        return (a['representation'] < b['representation']
                ? -1
                : a['representation'] > b['representation']
                ? 1
                : a['id'] - b['id']);
    });
    permissions.PermissionsTable.prototype.buildTable.apply(this, [rows]);
}

$(function(){ // wait for page to finish loading before executing jQuery code
    // Security stuff to prevent cross-site scripting.
    noXSS();
    
    var table = new CompoundDatatypesTable($('#compounddatatypes'), is_user_admin);
    table.buildTable($.parseJSON($('#initial_data').text()));
});