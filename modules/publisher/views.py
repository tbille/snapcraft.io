import datetime
import flask
import hashlib
import modules.public.views as public_views
import modules.publisher.api as api
from dateutil import relativedelta
from json import dumps, loads
from operator import itemgetter


def get_account():
    account = api.get_account()
    if 'redirect' in account:
        return account['redirect']

    error_list = []
    if 'error_list' in account:
        for error in account['error_list']:
            if error['code'] == 'user-not-ready':
                if 'has not signed agreement' in error['message']:
                    return flask.redirect('/account/agreement')
            else:
                error_list.append(error)

    user_snaps = []
    if '16' in account['snaps']:
        user_snaps = account['snaps']['16']

    return flask.render_template(
        'account.html',
        namespace=account['namespace'],
        user_snaps=user_snaps,
        user=flask.session['openid'],
        error_list=error_list
    )


def get_agreement():
    return flask.render_template('developer_programme_agreement.html')


def post_agreement():
    agreed = flask.request.form.get('i_agree')

    if agreed == 'on':
        api.post_agreement(True)
        return flask.redirect('/account')
    else:
        return flask.render_template('developer_programme_agreement.html')


def publisher_snap_measure(snap_name):
    """
    A view to display the snap measure page for specific snaps.

    This queries the snapcraft API (api.snapcraft.io) and passes
    some of the data through to the publisher/measure.html template,
    with appropriate sanitation.
    """
    details = api.get_snap_info(snap_name)
    metric_period = flask.request.args.get('period', default='30d', type=str)
    metric_bucket = ''.join([i for i in metric_period if not i.isdigit()])
    metric_period_int = int(metric_period[:-1])

    installed_base_metric = flask.request.args.get(
        'active-devices',
        default='version',
        type=str
    )

    today = datetime.datetime.utcnow().date()
    end = today - relativedelta.relativedelta(days=1)
    start = None
    if metric_bucket == 'd':
        start = end - relativedelta.relativedelta(days=metric_period_int)
    elif metric_bucket == 'm':
        start = end - relativedelta.relativedelta(months=metric_period_int)

    if installed_base_metric == 'version':
        installed_base = "weekly_installed_base_by_version"
    elif installed_base_metric == 'os':
        installed_base = "weekly_installed_base_by_operating_system"
    metrics_query_json = {
        "filters": [
            {
                "metric_name": installed_base,
                "snap_id": details['snap_id'],
                "start": start.strftime('%Y-%m-%d'),
                "end": end.strftime('%Y-%m-%d')
            },
            {
                "metric_name": "weekly_installed_base_by_country",
                "snap_id": details['snap_id'],
                "start": end.strftime('%Y-%m-%d'),
                "end": end.strftime('%Y-%m-%d')
            }
        ]
    }

    metrics_response_json = api.get_publisher_metrics(json=metrics_query_json)

    active_devices = metrics_response_json['metrics'][0]
    active_devices['series'] = sorted(
        active_devices['series'],
        key=itemgetter('name')
    )
    latest_active_devices = 0

    for series_index, series in enumerate(active_devices['series']):
        for index, value in enumerate(series['values']):
            if value is None:
                active_devices['series'][series_index]['values'][index] = 0
        values = series['values']
        if len(values) == len(active_devices['buckets']):
            latest_active_devices += values[len(values)-1]

    active_devices = {
        'series': active_devices['series'],
        'buckets': active_devices['buckets']
    }

    users_by_country = public_views.normalize_metrics(
        metrics_response_json['metrics'][1]['series']
    )

    country_data = public_views.build_country_info(
        users_by_country,
        True
    )
    territories_total = 0
    for data in country_data.values():
        if data['number_of_users'] > 0:
            territories_total += 1

    context = {
        # Data direct from details API
        'snap_name': snap_name,
        'snap_title': details['title'],
        'metric_period': metric_period,
        'active_device_metric': installed_base_metric,

        # Metrics data
        'latest_active_devices': "{:,}".format(latest_active_devices),
        'active_devices': active_devices,
        'territories_total': territories_total,
        'territories': country_data,

        # Context info
        'is_linux': 'Linux' in flask.request.headers['User-Agent']
    }

    return flask.render_template(
        'publisher/measure.html',
        **context
    )


def get_market_snap(snap_name):
    snap_details = api.get_snap_info(snap_name)

    context = {
        "snap_id": snap_details['snap_id'],
        "snap_name": snap_details['snap_name'],
        "title": snap_details['title'],
        "summary": snap_details['summary'],
        "description": snap_details['description'],
        "license": snap_details['license'],
        "icon_url": snap_details['icon_url'],
        "publisher_name": snap_details['publisher_name'],
        "screenshot_urls": snap_details['screenshot_urls'],
        "contact": snap_details['contact'],
        "website": snap_details['website']
    }

    return flask.render_template(
        'publisher/market.html',
        **context
    )


def snap_release(snap_name):
    snap_id = api.get_snap_id(snap_name)
    status_json = api.get_snap_status(snap_id)

    return flask.render_template(
        'publisher/release.html',
        snap_name=snap_name,
        status=status_json,
    )


def build_image_info(image, image_type):
    """
    Build info json structure for image upload
    Return json oject with useful informations for the api
    """
    hasher = hashlib.sha256(image.read())
    hash_final = hasher.hexdigest()
    image.seek(0)

    return {
        "key": image.filename,
        "type": image_type,
        "filename": image.filename,
        "hash": hash_final
    }


def post_market_snap(snap_name):
    snap_id = api.get_snap_id(snap_name)

    if 'submit_revert' in flask.request.form:
        flask.flash("All changes reverted.", 'information')
    else:
        error_list = []
        info = []
        images_files = []
        images_json = None

        # Add existing screenshots
        current_screenshots = api.snap_screenshots(
            snap_id
        )
        state_screenshots = loads(flask.request.form['state'])['images']
        for state_screenshot in state_screenshots:
            for current_screenshot in current_screenshots:
                if state_screenshot['url'] == current_screenshot['url']:
                    info.append(current_screenshot)

        # Add new icon
        icon = flask.request.files.get('icon')
        if icon is not None:
            info.append(build_image_info(icon, 'icon'))
            images_files.append(icon)

        # Add new screenshots
        new_screenshots = flask.request.files.getlist('screenshots')
        for new_screenshot in new_screenshots:
            for state_screenshot in state_screenshots:
                is_new = state_screenshot['status'] == 'new'
                is_same = state_screenshot['name'] == new_screenshot.filename
                if is_new and is_same:
                    info.append(build_image_info(new_screenshot, 'screenshot'))
                    images_files.append(new_screenshot)

        images_json = {'info': dumps(info)}
        screenshots_response = api.snap_screenshots(
            snap_id,
            images_json,
            images_files
        )

        if 'error_list' in screenshots_response:
            error_list = error_list + screenshots_response['error_list']

        whitelist = [
            'title',
            'summary',
            'description',
            'contact',
            'website',
            'keywords',
            'license',
            'price',
            'blacklist_countries',
            'whitelist_countries'
        ]

        body_json = {
            key: flask.request.form[key]
            for key in whitelist if key in flask.request.form
        }

        metadata = api.snap_metadata(
            flask.request.form['snap_id'],
            body_json
        )
        if 'error_list' in metadata:
            error_list = error_list + metadata['error_list']

        if error_list:
            snap_details = api.get_snap_info(snap_name)
            context = {
                "snap_id": snap_details['snap_id'],
                "snap_name": snap_details['snap_name'],
                "title": snap_details['title'],
                "summary": snap_details['summary'],
                "description": snap_details['description'],
                "license": snap_details['license'],
                "icon_url": snap_details['icon_url'],
                "publisher_name": snap_details['publisher_name'],
                "screenshot_urls": snap_details['screenshot_urls'],
                "error_list": error_list
            }

            return flask.render_template(
                'publisher/market.html',
                **context
            )

        flask.flash("Changes applied successfully.", 'positive')

    return flask.redirect(
        "/account/snaps/{snap_name}/market".format(
            snap_name=snap_name
        )
    )
