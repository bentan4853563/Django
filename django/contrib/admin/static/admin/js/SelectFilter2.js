/*global SelectBox, gettext, interpolate, quickElement, SelectFilter*/
/*
SelectFilter2 - Turns a multiple-select box into a filter interface.

Requires core.js and SelectBox.js.
*/
'use strict';
{
    window.SelectFilter = {
        init: function(field_id, field_name, is_stacked) {
            if (field_id.match(/__prefix__/)) {
                // Don't initialize on empty forms.
                return;
            }
            const from_box = document.getElementById(field_id);
            from_box.id += '_from'; // change its ID
            from_box.className = 'filtered';

            for (const p of from_box.parentNode.getElementsByTagName('p')) {
                if (p.classList.contains("info")) {
                    // Remove <p class="info">, because it just gets in the way.
                    from_box.parentNode.removeChild(p);
                } else if (p.classList.contains("help")) {
                    // Move help text up to the top so it isn't below the select
                    // boxes or wrapped off on the side to the right of the add
                    // button:
                    from_box.parentNode.insertBefore(p, from_box.parentNode.firstChild);
                }
            }

            // <div class="selector"> or <div class="selector stacked">
            const selector_div = quickElement('div', from_box.parentNode);
            // Make sure the selector div is at the beginning so that the
            // add link would be displayed to the right of the widget.
            from_box.parentNode.prepend(selector_div);
            selector_div.className = is_stacked ? 'selector stacked' : 'selector';

            // <div class="selector-available">
            const selector_available = quickElement('div', selector_div);
            selector_available.className = 'selector-available';
            const title_available = quickElement('h2', selector_available, interpolate(gettext('Available %s') + ' ', [field_name]));
            quickElement(
                'span', title_available, '',
                'class', 'help help-tooltip help-icon',
                'title', interpolate(
                    gettext(
                        'This is the list of available %s. You may choose some by ' +
                        'selecting them in the box below and then clicking the ' +
                        '"Choose" arrow between the two boxes.'
                    ),
                    [field_name]
                )
            );

            const filter_p = quickElement('p', selector_available, '', 'id', field_id + '_filter');
            filter_p.className = 'selector-filter';

            const search_filter_label = quickElement('label', filter_p, '', 'for', field_id + '_input');

            quickElement(
                'span', search_filter_label, '',
                'class', 'help-tooltip search-label-icon',
                'title', interpolate(gettext("Type into this box to filter down the list of available %s."), [field_name])
            );

            filter_p.appendChild(document.createTextNode(' '));

            const filter_input = quickElement('input', filter_p, '', 'type', 'text', 'placeholder', gettext("Filter"));
            filter_input.id = field_id + '_input';

            selector_available.appendChild(from_box);
            const choose_all = quickElement('a', selector_available, gettext('Choose all'), 'title', interpolate(gettext('Click to choose all %s at once.'), [field_name]), 'href', '#', 'id', field_id + '_add_all_link');
            choose_all.className = 'selector-chooseall';

            // <ul class="selector-chooser">
            const selector_chooser = quickElement('ul', selector_div);
            selector_chooser.className = 'selector-chooser';
            const add_link = quickElement('a', quickElement('li', selector_chooser), gettext('Choose'), 'title', gettext('Choose'), 'href', '#', 'id', field_id + '_add_link');
            add_link.className = 'selector-add';
            const remove_link = quickElement('a', quickElement('li', selector_chooser), gettext('Remove'), 'title', gettext('Remove'), 'href', '#', 'id', field_id + '_remove_link');
            remove_link.className = 'selector-remove';

            // <div class="selector-chosen">
            const selector_chosen = quickElement('div', selector_div, '', 'id', field_id + '_selector_chosen');
            selector_chosen.className = 'selector-chosen';
            const title_chosen = quickElement('h2', selector_chosen, interpolate(gettext('Chosen %s') + ' ', [field_name]));
            quickElement(
                'span', title_chosen, '',
                'class', 'help help-tooltip help-icon',
                'title', interpolate(
                    gettext(
                        'This is the list of chosen %s. You may remove some by ' +
                        'selecting them in the box below and then clicking the ' +
                        '"Remove" arrow between the two boxes.'
                    ),
                    [field_name]
                )
            );
            
            const filter_selected_p = quickElement('p', selector_chosen, '', 'id', field_id + '_filter_selected');
            filter_selected_p.className = 'selector-filter';

            const search_filter_selected_label = quickElement('label', filter_selected_p, '', 'for', field_id + '_selected_input');

            quickElement(
                'span', search_filter_selected_label, '',
                'class', 'help-tooltip search-label-icon',
                'title', interpolate(gettext("Type into this box to filter down the list of selected %s."), [field_name])
            );

            filter_selected_p.appendChild(document.createTextNode(' '));

            const filter_selected_input = quickElement('input', filter_selected_p, '', 'type', 'text', 'placeholder', gettext("Filter"));
            filter_selected_input.id = field_id + '_selected_input';

            const to_box = quickElement('select', selector_chosen, '', 'id', field_id + '_to', 'multiple', '', 'size', from_box.size, 'name', from_box.name);
            to_box.className = 'filtered';
            
            const warning_footer = quickElement('div', selector_chosen, '', 'class', 'list-footer-display');
            quickElement('span', warning_footer, '', 'id', field_id + '_list-footer-display-text');
            quickElement('span', warning_footer, ' (click to clear)', 'class', 'list-footer-display__clear');
            
            const clear_all = quickElement('a', selector_chosen, gettext('Remove all'), 'title', interpolate(gettext('Click to remove all chosen %s at once.'), [field_name]), 'href', '#', 'id', field_id + '_remove_all_link');
            clear_all.className = 'selector-clearall';

            from_box.name = from_box.name + '_old';

            // Set up the JavaScript event handlers for the select box filter interface
            const move_selection = function(e, elem, move_func, from, to) {
                if (elem.classList.contains('active')) {
                    move_func(from, to);
                    SelectFilter.refresh_icons(field_id);
                    SelectFilter.refresh_filtered_selects(field_id);
                    SelectFilter.refresh_filtered_warning(field_id);
                }
                e.preventDefault();
            };
            choose_all.addEventListener('click', function(e) {
                move_selection(e, this, SelectBox.move_all, field_id + '_from', field_id + '_to');
            });
            add_link.addEventListener('click', function(e) {
                move_selection(e, this, SelectBox.move, field_id + '_from', field_id + '_to');
            });
            remove_link.addEventListener('click', function(e) {
                move_selection(e, this, SelectBox.move, field_id + '_to', field_id + '_from');
            });
            clear_all.addEventListener('click', function(e) {
                move_selection(e, this, SelectBox.move_all, field_id + '_to', field_id + '_from');
            });
            warning_footer.addEventListener('click', function(e) {
                filter_selected_input.value = '';
                SelectBox.filter(field_id + '_to', '');
                SelectFilter.refresh_filtered_warning(field_id);
                SelectFilter.refresh_icons(field_id);
            });
            filter_input.addEventListener('keypress', function(e) {
                SelectFilter.filter_key_press(e, field_id, '_from', '_to');
            });
            filter_input.addEventListener('keyup', function(e) {
                SelectFilter.filter_key_up(e, field_id, '_from');
            });
            filter_input.addEventListener('keydown', function(e) {
                SelectFilter.filter_key_down(e, field_id, '_from', '_to');
            });
            filter_selected_input.addEventListener('keypress', function(e) {
                SelectFilter.filter_key_press(e, field_id, '_to', '_from');
            });
            filter_selected_input.addEventListener('keyup', function(e) {
                SelectFilter.filter_key_up(e, field_id, '_to', '_selected_input');
            });
            filter_selected_input.addEventListener('keydown', function(e) {
                SelectFilter.filter_key_down(e, field_id, '_to', '_from');
            });
            selector_div.addEventListener('change', function(e) {
                if (e.target.tagName === 'SELECT') {
                    SelectFilter.refresh_icons(field_id);
                }
            });
            selector_div.addEventListener('dblclick', function(e) {
                if (e.target.tagName === 'OPTION') {
                    if (e.target.closest('select').id === field_id + '_to') {
                        SelectBox.move(field_id + '_to', field_id + '_from');
                    } else {
                        SelectBox.move(field_id + '_from', field_id + '_to');
                    }
                    SelectFilter.refresh_icons(field_id);
                }
            });
            from_box.closest('form').addEventListener('submit', function() {
                SelectBox.filter(field_id + '_to', '');
                SelectBox.select_all(field_id + '_to');
            });
            SelectBox.init(field_id + '_from');
            SelectBox.init(field_id + '_to');
            // Move selected from_box options to to_box
            SelectBox.move(field_id + '_from', field_id + '_to');

            // Initial icon refresh
            SelectFilter.refresh_icons(field_id);
        },
        any_selected: function(field) {
            // Temporarily add the required attribute and check validity.
            field.required = true;
            const any_selected = field.checkValidity();
            field.required = false;
            return any_selected;
        },
        refresh_filtered_warning: function(field_id) {
            const count = SelectBox.get_hidden_node_count(field_id + '_to');
            const selector = document.getElementById(field_id + '_selector_chosen');
            const warning = document.getElementById(field_id + '_list-footer-display-text');
            selector.className = selector.className.replace('selector-chosen--with-filtered', '');
            warning.textContent = interpolate(ngettext(
                '%s selected option not visible',
                '%s selected options not visible',
                count
            ), [count]);
            if(count > 0) {
                selector.className += ' selector-chosen--with-filtered';
            }
        },
        refresh_filtered_selects: function(field_id) {
            SelectBox.filter(field_id + '_from', document.getElementById(field_id + "_input").value);
            SelectBox.filter(field_id + '_to', document.getElementById(field_id + "_selected_input").value);
        },
        refresh_icons: function(field_id) {
            const from = document.getElementById(field_id + '_from');
            const to = document.getElementById(field_id + '_to');
            // Active if at least one item is selected
            document.getElementById(field_id + '_add_link').classList.toggle('active', SelectFilter.any_selected(from));
            document.getElementById(field_id + '_remove_link').classList.toggle('active', SelectFilter.any_selected(to));
            // Active if the corresponding box isn't empty
            document.getElementById(field_id + '_add_all_link').classList.toggle('active', from.querySelector('option'));
            document.getElementById(field_id + '_remove_all_link').classList.toggle('active', to.querySelector('option'));
            SelectFilter.refresh_filtered_warning(field_id);
        },
        filter_key_press: function(event, field_id, source, target) {
            const source_box = document.getElementById(field_id + source);
            // don't submit form if user pressed Enter
            if ((event.which && event.which === 13) || (event.keyCode && event.keyCode === 13)) {
                source_box.selectedIndex = 0;
                SelectBox.move(field_id + source, field_id + target);
                source_box.selectedIndex = 0;
                event.preventDefault();
            }
        },
        filter_key_up: function(event, field_id, source, filter_input) {
            const input = filter_input || '_input';
            const source_box = document.getElementById(field_id + source);
            const temp = source_box.selectedIndex;
            SelectBox.filter(field_id + source, document.getElementById(field_id + input).value);
            source_box.selectedIndex = temp;
            SelectFilter.refresh_filtered_warning(field_id);
            SelectFilter.refresh_icons(field_id);
        },
        filter_key_down: function(event, field_id, source, target) {
            const source_box = document.getElementById(field_id + source);
            // right key (39) or left key (37)
            const direction = source === '_from' ? 39 : 37;
            // right arrow -- move across
            if ((event.which && event.which === direction) || (event.keyCode && event.keyCode === direction)) {
                const old_index = source_box.selectedIndex;
                SelectBox.move(field_id + source, field_id + target);
                SelectFilter.refresh_filtered_selects(field_id);
                SelectFilter.refresh_filtered_warning(field_id);
                source_box.selectedIndex = (old_index === source_box.length) ? source_box.length - 1 : old_index;
                return;
            }
            // down arrow -- wrap around
            if ((event.which && event.which === 40) || (event.keyCode && event.keyCode === 40)) {
                source_box.selectedIndex = (source_box.length === source_box.selectedIndex + 1) ? 0 : source_box.selectedIndex + 1;
            }
            // up arrow -- wrap around
            if ((event.which && event.which === 38) || (event.keyCode && event.keyCode === 38)) {
                source_box.selectedIndex = (source_box.selectedIndex === 0) ? source_box.length - 1 : source_box.selectedIndex - 1;
            }
        }
    };

    window.addEventListener('load', function(e) {
        document.querySelectorAll('select.selectfilter, select.selectfilterstacked').forEach(function(el) {
            const data = el.dataset;
            SelectFilter.init(el.id, data.fieldName, parseInt(data.isStacked, 10));
        });
    });
}
