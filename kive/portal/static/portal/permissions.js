var permissions = (function() {
    "use strict";
    var my = {};
    
    /** A base class for building a table with permissions columns.
     * 
     * Also includes a lock button for administrators to show all records and
     * links to remove or redact records.
     * 
     * Derived classes should register their own columns. For example:
     * 
     *  var BookTable = function($table, is_user_admin) {
     *      permissions.PermissionsTable.call(this, $table, is_user_admin);
     *      this.list_url = "api/books";
     *      this.registerColumn("Title", "title");
     *      this.registerColumn("Author", "author");
     *      this.registerColumn("Pub. Date", function($td, row) {
     *          $td.text(permissions.formatDate(row.published));
     *      });
     *  }
     *  BookTable.prototype = Object.create(permissions.PermissionsTable.prototype);
     *  BookTable.prototype.constructor = BookTable; // http://stackoverflow.com/a/8454111/4794
     *  
     *  Special columns that derived classes may include in the data rows are:
     *  
     *  user: the owner of the row
     *  users_allowed: user names that have been granted access
     *  groups_allowed: group names that have been granted access
     *  removal_plan: a URL to retrieve a removal plan summary
     *  redaction_plan: a URL to retrieve a redaction plan summary
     *  url: if removal_plan or redaction_plan are present, this is used for
     *      DELETE or PATCH requests.
     *  
     *  Derived classes can also set this.reload_interval in milliseconds for
     *  the table to continuously poll the server.
     */
    my.PermissionsTable = function($table, is_user_admin) {
        this.$table = $table;
        this.is_user_admin = is_user_admin;
        this.is_locked = true;
        this.image_path = '/static/portal/img';
        this.registered_columns = [];
        this.$lockImage = $('<img/>');
        this.$lockSpan = $('<span/>');
    };
    
    /**
     * Register a column to be added to the table.
     * 
     *  @param header: a string to label the column header
     *  @param source: Either a field name, or a function to build a cell in
     *      the column. Function signature must be f($td, row, data).
     *  @param data: optional data object to pass to the source function
     */
    my.PermissionsTable.prototype.registerColumn = function(
            header,
            source,
            data) {
        var column = { header: header, builder: source, data: data };
        if (typeof column.builder !== 'function') {
            column.builder = defaultBuilder;
            column.data = source;
        }
        this.registered_columns.push(column);
    };
    
    function defaultBuilder($td, row, field_name) {
        $td.text(row[field_name]);
    }
    
    my.PermissionsTable.prototype.buildTable = function(rows) {
        var $tr,
            $a,
            permissions_table = this,
            lock_icon = this.image_path +
                (this.is_locked ? '/lock-locked-2x.png' : '/lock-unlocked-2x.png');
        
        if (this.$tbody !== undefined) {
            this.$tbody.empty();
        }
        else if (rows.length > 0) {
            $tr = $('<tr/>');
            if ('user' in rows[0]) {
                this.registerColumn('Creator', 'user');
            }
            if ('users_allowed' in rows[0]) {
                this.registerColumn(
                        'Users with access',
                        buildListCell,
                'users_allowed');
            }
            if ('groups_allowed' in rows[0])
            this.registerColumn(
                    'Groups with access',
                    buildListCell,
                    'groups_allowed');
            this.buildHeaders($tr);
            this.$table.append($('<thead/>').append($tr));
            this.$tbody = $('<tbody/>');
            this.$table.append(this.$tbody);
        }
        this.$lockImage.attr('src', lock_icon);
        this.$lockSpan.text(this.is_locked ? '' : 'Administrator');
        $.each(rows, function() {
            var $a,
                row = this;
            $tr = $('<tr/>');
            permissions_table.buildCells($tr, row);
            permissions_table.$tbody.append($tr);
        });
        
        // Schedule reload if it's been requested.
        if (this.reload_interval !== undefined) {
            this.timeout_id = setTimeout(
                    function() {
                        permissions_table.reloadTable();
                    },
                    this.reload_interval);
        }
    };
    
    my.PermissionsTable.prototype.buildHeaders = function($tr) {
        this.buildPermissionHeaders($tr);
    };
    
    my.PermissionsTable.prototype.buildCells = function($tr, row) {
        this.buildPermissionCells($tr, row);
    };
    
    my.PermissionsTable.prototype.buildPermissionHeaders = function($tr) {
        var $a;
        $.each(this.registered_columns, function() {
            $tr.append($('<th/>').text(this.header));
        });
        if (this.is_user_admin) {
            $a = ($('<a href="javascript:void(0)"/>')
                    .append(this.$lockImage, this.$lockSpan)
                    .click(this, clickLock));

            $tr.append($('<th colspan="2"/>')
                    .addClass('lock')
                    .append($('<div/>').append($a)));
        }
    };
    
    my.PermissionsTable.prototype.buildPermissionCells = function($tr, row) {
        var $td;
        $.each(this.registered_columns, function() {
            var $td = $('<td/>');
            this.builder($td, row, this.data);
            $tr.append($td);
        });
        $td = $('<td/>');
        if ( ! this.is_locked) {
            if (row.removal_plan !== undefined && row.removal_plan !== null) {
                $tr.append($('<td/>').append($('<a/>')
                        .attr('plan', row.removal_plan)
                        .attr('href', row.url)
                        .text('Remove')
                        .click(this, clickRemove)));
            }
            if (row.redaction_plan !== undefined && row.redaction_plan !== null) {
                $tr.append($('<td/>').append($('<a/>')
                        .attr('plan', row.redaction_plan)
                        .attr('href', row.url)
                        .text('Redact')
                        .click(this, clickRedact)));
            }
        }
        if ($td.children().length === 0) {
            $td.html('&nbsp;');
        }
        $tr.append($td);
    };
    
    my.PermissionsTable.prototype.toggleLock = function() {
        this.is_locked = ! this.is_locked;
        this.reloadTable();
    };
    
    my.PermissionsTable.prototype.reloadTable = function() {
        var permissions_table = this;
        if (permissions_table.timeout_id !== undefined) {
            window.clearTimeout(permissions_table.timeout_id);
            permissions_table.timeout_id = undefined;
        }
        if (permissions_table.ajax_request !== undefined) {
            permissions_table.ajax_request.abort();
            permissions_table.ajax_request = undefined;
        }
        permissions_table.ajax_request = $.getJSON(
                permissions_table.list_url,
                permissions_table.getQueryParams()).done(function(response) {
                    var rows;
                    rows = permissions_table.extractRows(response);
                    permissions_table.buildTable(rows);
                    permissions_table.setCaption("");
                }).fail(function(request) {
                    var response = request.responseJSON,
                        detail = (
                                response ?
                                response.detail :
                                "Failed to reload table");
                    permissions_table.$tbody.empty();
                    permissions_table.setCaption(detail);
                }).always(function() {
                    permissions_table.ajax_request = undefined;
                });
    };
    
    my.PermissionsTable.prototype.setCaption = function(text) {
        var $caption = this.$table.find('caption');
        if ( ! $caption.length) {
            $caption = $('<caption/>');
            this.$table.append($caption);
        }
        $caption.text(text);
    };

    /** Get query parameters from the current state of the table and page.
     * 
     */
    my.PermissionsTable.prototype.getQueryParams = function() {
        return { is_granted: this.is_locked };
    };
    
    /** Extract row data from an AJAX response.
     * @param response: the JSON object returned from the server
     */
    my.PermissionsTable.prototype.extractRows = function(response) {
        return response;
    };
    
    /** Choose which redaction field to set when redacting.
     * @param plan_url: the redaction plan URL that this field should enact
     */
    my.PermissionsTable.prototype.getRedactionField = function(plan_url) {
        return "is_redacted";
    };
    
    function buildListCell($td, row, field_name) {
        var $ul = $('<ul/>').addClass('cell-list'),
            names = row[field_name];
        $.each(names, function() {
            $ul.append($('<li/>').text(this));
        });
        $td.append($ul);
    }
    
    function clickLock(event) {
        event.preventDefault();
        var permissions_table = event.data;
        permissions_table.toggleLock();
    }

    my.PermissionsTable.prototype.buildConfirmationMessage = function(
            plan,
            action) {
        var message = "This will " + action + ":\n";
        for(var k in plan) {
            if (plan[k] !== 0) {
                message += plan[k] + " " + k + "\n";
            }
        }

        return message + "Are you sure?";
    };

    function clickRemove(event) {
        // jQuery sets `this` for event handlers
        //jshint validthis: true
        var $a = $(this),
            permissions_table = event.data;
        event.preventDefault();
        $.getJSON(
                $a.attr('plan'),
                {},
                function (plan) {
                    var message = permissions_table.buildConfirmationMessage(
                            plan,
                            "remove");
                    if (window.confirm(message)) {
                        $.ajax({
                            url: $a.attr('href'),
                            method: 'DELETE',
                            success: function() {
                                permissions_table.reloadTable();
                            }
                        }).fail(
                            function (request) {
                                var response = request.responseJSON,
                                    detail = (
                                            response ?
                                            response.detail :
                                            "Failed to remove");
                                window.alert(detail);
                            }
                        );
                    }
                });
    }

    function clickRedact(event){
        // jQuery sets `this` for event handlers
        //jshint validthis: true
        var $a = $(this),
            permissions_table = event.data,
            plan_url = $a.attr('plan');
        event.preventDefault();
        $.getJSON(
                plan_url,
                {},
                function (plan) {
                    var message = permissions_table.buildConfirmationMessage(
                            plan,
                            "redact"),
                        redaction_field = permissions_table.getRedactionField(
                                plan_url),
                        redaction_data = {};
                    if (window.confirm(message)) {
                        redaction_data[redaction_field] = true;
                        $.ajax({
                            url: $a.attr('href'),
                            method: 'PATCH',
                            data: redaction_data,
                            success: function() {
                                permissions_table.reloadTable();
                            }
                        }).fail(
                            function (request) {
                                var response = request.responseJSON,
                                    detail = (
                                            response ?
                                            response.detail :
                                            "Failed to redact");
                                window.alert(detail);
                            }
                        );
                    }
                });
    }
    
    my.formatDate = function(text) {
        var monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            date = new Date(text),
            min = date.getMinutes();
        if (Number.isNaN(min)) {
            return null;
        }
        if (min < 10) {
            min = "0" + min;
        }
        return (date.getDate() + " " + monthNames[date.getMonth()] + " " + 
                date.getFullYear() + " " + date.getHours() + ":" + min);
    };
    
    /**
     * Build and apply a set of search filters.
     * 
     * @param $active: a jQuery object that will hold all the active filters
     * @param onChange: a function that is called when a filter is added or
     *  removed
     */
    my.FilterSet = function($active, onChange) {
        this.$active = $active;
        this.onChange = onChange;
    };
    
    function addFilter(filterSet, key, value) {
        var $filter,
            $duplicates;
        $filter = $('<div class="filter"/>').data({ key: key, val: value });
        $filter.append(
                $('<span class="field"/>').text(key + ':'),
                $('<span class="value"/>').text(value));
        $duplicates = $('div.filter', filterSet.$active).filter(function() {
            var $f = $(this);
            return $f.data('key') == key && $f.data('val') == value;
        });
        if ( ! $duplicates.length) {
            $filter.append($('<a class="remove">&times;</a>').click(function() {
                $filter.detach();
                filterSet.onChange();
            }));
            filterSet.$active.prepend($filter);
        }
    }
    
    my.FilterSet.prototype.add = function(key, value) {
        addFilter(this, key, value);
        this.onChange();
    };
    
    my.FilterSet.prototype.addFromForm = function(form) {
        var filterSet = this,
            $fields = $('input[type="text"], input:checked', form);
        $fields = $($fields.get().reverse());
        $fields.each(function() {
            var $field = $(this),
                value = $field.val();
            if (value.length === 0) {
                return;
            }
            if ($field.is('.datetime')) {
                value = my.formatDate(value);
            }
            addFilter(
                    filterSet,
                    $field.attr('name'),
                    $field.is(':checked') ? undefined : value);
            if ($field.is(':checked')) {
                $field.attr('checked', false);
            }
            else {
                $field.val('');
            }
        });
        this.onChange();
    };
    
    my.FilterSet.prototype.getFilters = function() {
        var filters = [];
        this.$active.find('.filter').each(function() {
            filters.push($(this).data());
        });
        return filters;
    };

    return my;
}());
