"use strict";

var OutputsTable = function($table, is_user_admin, run_id) {
    permissions.PermissionsTable.call(this, $table, is_user_admin);
    var outputsTable = this;
    this.list_url = "/api/runs/" + run_id + "/run_outputs/";
    this.registerColumn("Source", "step_name");
    this.registerColumn("Input/Output", function($td, output) {
        $td.text(output.display);
        outputsTable.setErrors($td, output.errors);
    });
    this.registerColumn("Size", "size");
    this.registerColumn("Date created", "date");
    this.registerColumn("", function($td, output) {
        var href;
        if (output.id !== null) {
            href = '../../' + output.type + '_view/' + output.id + "?run_id=" + run_id + "&view_results";
            $td.append($('<a>View</a>').attr('href', href));
        }
    });
    this.registerColumn("", function($td, output) {
        var href;
        if (output.id !== null) {
            href = '../../' + output.type + '_download/' + output.id + "?run_id=" + run_id + "&view_results";
            $td.append($('<a>Download</a>').attr('href', href));
        }
    });
};
OutputsTable.prototype = Object.create(
        permissions.PermissionsTable.prototype);

OutputsTable.prototype.extractRows = function(response) {
    if (this.$remove_link !== undefined) {
        this.$remove_link.toggle( ! this.is_locked);
    }
    else if ( ! this.is_locked) {
        this.$remove_link = $('<a href="#">Remove Run</a>').click(
                this,
                clickRemove);
        var $tr = this.$table.find('thead tr');
        $tr.append($('<th/>').append(this.$remove_link));
    }
    if (response === null) {
        return [];
    }
    return response.input_summary.concat(response.output_summary);
};

OutputsTable.prototype.getRedactionField = function(plan_url) {
    var output_ending = "output_redaction_plan/",
        error_ending  = "/error_redaction_plan/",
        ending = plan_url.substr(plan_url.length - output_ending.length);
    return (ending === output_ending
            ? "output_redacted"
            : ending === error_ending
            ? "error_redacted"
            : "is_redacted");
};

function clickRemove(e) {
    var $a = $(this),
        permissions_table = event.data,
        run_url = '/api/runs/' + run_id,
        plan_url = run_url + '/removal_plan/';
    e.preventDefault();
    $.getJSON(
            plan_url,
            {},
            function (plan) {
                var message = permissions_table.buildConfirmationMessage(
                        plan,
                        "remove");
                if (window.confirm(message)) {
                    $.ajax({
                        url: run_url,
                        method: 'DELETE',
                        success: function() {
                            window.location = '../../runs';
                        }
                    });
                }
            });
}

$(function(){ // wait for page to finish loading before executing jQuery code
    // Security stuff to prevent cross-site scripting.
    noXSS();
    
    var table = new OutputsTable($('#outputs'), is_user_admin, run_id),
        $permissions_widget = $("#permissions_widget"),
        $users_widget = $permissions_widget.find("#id_permissions_0"),
        $groups_widget = $permissions_widget.find("#id_permissions_1"),
        $edit_link_div = $("#edit_permissions"),
        $loading_msg = $("#loading_message");

    table.buildTable(table.extractRows($.parseJSON($('#outputs_json').text())));

    if (is_owner || is_admin) {
        $loading_msg.hide();
        $permissions_widget.hide();

        $("a", $edit_link_div)
            .on("click", fillPermissionsForm);

        fillPermissionsForm.call($("a", $edit_link_div));
    }

    function fillPermissionsForm(e) {
        if (e) e.preventDefault();

        $edit_link_div.hide();
        $loading_msg.show();

        // Retrieve the list of eligible users and groups that we can add permissions to.
        $.getJSON($(this).attr("href")).done(
            function (response) {
                // The response should be a list of two lists, where the first list is
                // eligible users and the second list is eligible groups.
                // Both lists should be of 2-tuples (pk, username|groupname).
                $.each(response.users, function () {
                    $("<option>")
                        .attr("value", this.id)
                        .text(this.username)
                        .appendTo($users_widget);
                });

                $.each(response.groups, function () {
                    $("<option>")
                        .attr("value", this.id)
                        .text(this.name)
                        .appendTo($groups_widget);
                });

                $loading_msg.hide();
                $permissions_widget.show()
                    .find('.permissions-widget').trigger('sync');
            }
        ).fail(
            function (request) {
                var response = request.responseJSON,
                    detail = (
                            response ?
                            response.detail :
                            "Error while finding eligible users and groups");
                $loading_msg.text(detail);
            }
        );
    }
});
