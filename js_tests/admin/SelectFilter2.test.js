/* global QUnit, SelectFilter */
'use strict';

QUnit.module('admin.SelectFilter2');

QUnit.test('init', function(assert) {
    const $ = django.jQuery;
    $('<form><select id="id"></select></form>').appendTo('#qunit-fixture');
    $('<option value="0">A</option>').appendTo('#id');
    SelectFilter.init('id', 'things', 0);
    assert.equal($('.selector-available h2').text().trim(), "Available things");
    assert.equal($('.selector-chosen h2').text().trim(), "Chosen things");
    assert.equal($('.selector-chosen select')[0].getAttribute('multiple'), '');
    assert.equal($('.selector-chooseall').text(), "Choose all");
    assert.equal($('.selector-add').text(), "Choose");
    assert.equal($('.selector-remove').text(), "Remove");
    assert.equal($('.selector-clearall').text(), "Remove all");
});

QUnit.test('filtering available options', function(assert) {
    const $ = django.jQuery;
    $('<form><select multiple id="select"></select></form>').appendTo('#qunit-fixture');
    $('<option value="1" title="Red">Red</option>').appendTo('#select');
    $('<option value="2" title="Blue">Blue</option>').appendTo('#select');
    $('<option value="3" title="Green">Green</option>').appendTo('#select');
    SelectFilter.init('select', 'items', 0);
    assert.equal($('#select_from option').length, 3);
    assert.equal($('#select_to option').length, 0);
    const done = assert.async();
    const search_term = 'r';
    const event = new KeyboardEvent('keyup', {'key': search_term});
    $('#select_input').val(search_term);
    SelectFilter.filter_key_up(event, 'select');
    setTimeout(() => {
        assert.equal($('#select_from option').length, 2);
        assert.equal($('#select_to option').length, 0);
        assert.equal($('#select_from option')[0].value, '1');
        assert.equal($('#select_from option')[1].value, '3');
        done();
    });
});

QUnit.test('filtering available options to nothing', function(assert) {
    const $ = django.jQuery;
    $('<form><select multiple id="select"></select></form>').appendTo('#qunit-fixture');
    $('<option value="1" title="Red">Red</option>').appendTo('#select');
    $('<option value="2" title="Blue">Blue</option>').appendTo('#select');
    $('<option value="3" title="Green">Green</option>').appendTo('#select');
    SelectFilter.init('select', 'items', 0);
    assert.equal($('#select_from option').length, 3);
    assert.equal($('#select_to option').length, 0);
    const done = assert.async();
    const search_term = 'x';
    const event = new KeyboardEvent('keyup', {'key': search_term});
    $('#select_input').val(search_term);
    SelectFilter.filter_key_up(event, 'select');
    setTimeout(() => {
        assert.equal($('#select_from option').length, 0);
        assert.equal($('#select_to option').length, 0);
        done();
    });
});

QUnit.test('selecting option', function(assert) {
    const $ = django.jQuery;
    $('<form><select multiple id="select"></select></form>').appendTo('#qunit-fixture');
    $('<option value="1" title="Red">Red</option>').appendTo('#select');
    $('<option value="2" title="Blue">Blue</option>').appendTo('#select');
    $('<option value="3" title="Green">Green</option>').appendTo('#select');
    SelectFilter.init('select', 'items', 0);
    assert.equal($('#select_from option').length, 3);
    assert.equal($('#select_to option').length, 0);
    // move to the right
    const done = assert.async();
    $('#select_from')[0].selectedIndex = 0;
    const event = new KeyboardEvent('keydown', {'keyCode': 39, 'charCode': 39});
    SelectFilter.filter_key_down(event, 'select');
    setTimeout(() => {
        assert.equal($('#select_from option').length, 2);
        assert.equal($('#select_to option').length, 1);
        assert.equal($('#select_to option')[0].value, '1');
        done();
    });
});
