import uuid
import functools
from flask import session
from werkzeug.local import LocalProxy
from .remote_app import RemoteApp
from ..oauth_client import OAUTH_CLIENT_PARAMS

__all__ = ['OAuth']
_req_token_tpl = '_{}_authlib_req_token_'


class OAuth(object):
    """Registry for oauth clients.

    :param app: the app instance of Flask

    Create an instance with Flask::

        oauth = OAuth(app, cache=cache)

    You can also pass the instance of Flask later::

        oauth = OAuth()
        oauth.init_app(app, cache=cache)

    :param app: Flask application instance
    :param cache: A cache instance that has .get .set and .delete methods
    :param fetch_token: a shared function to get current user's token
    :param update_token: a share function to update current user's token
    """

    def __init__(self, app=None, cache=None,
                 fetch_token=None, update_token=None):
        self._registry = {}
        self._clients = {}

        self.app = app
        self.fetch_token = fetch_token
        self.update_token = update_token
        self.cache = cache
        if app:
            self.init_app(app)

    def init_app(self, app, cache=None, fetch_token=None, update_token=None):
        """Init app with Flask instance."""
        self.app = app
        if cache is not None:
            self.cache = cache

        if fetch_token:
            self.fetch_token = fetch_token
        if update_token:
            self.update_token = update_token

        app.extensions = getattr(app, 'extensions', {})
        app.extensions['authlib.flask.client'] = self

    def create_client(self, name):
        if not self.app:
            raise RuntimeError('OAuth is not init with Flask app.')

        if name in self._clients:
            return self._clients[name]

        overwrite, kwargs = self._registry[name]
        compliance_fix = kwargs.pop('compliance_fix', None)
        client_cls = kwargs.pop('client_cls', RemoteApp)

        # update kwargs from app.config
        kwargs = self._update_config_kwargs(name, kwargs, overwrite)
        # generate kwargs for OAuthClient
        kwargs = self._generate_client_kwargs(name, kwargs)
        client = client_cls(name, **kwargs)
        if compliance_fix:
            client.compliance_fix = compliance_fix

        self._clients[name] = client
        return client

    def register(self, name, overwrite=False, **kwargs):
        """Registers a new remote application.

        :param name: Name of the remote application.
        :param overwrite: Overwrite existing config with Flask config.
        :param kwargs: Parameters for :class:`RemoteApp`.

        Find parameters from :class:`~authlib.client.OAuthClient`.
        When a remote app is registered, it can be accessed with
        *named* attribute::

            oauth.register('twitter', client_id='', ...)
            oauth.twitter.get('timeline')
        """
        self._registry[name] = (overwrite, kwargs)
        if self.app:
            return self.create_client(name)
        return LocalProxy(lambda: self.create_client(name))

    def _update_config_kwargs(self, name, kwargs, overwrite):
        for k in OAUTH_CLIENT_PARAMS:
            conf_key = '{}_{}'.format(name, k).upper()
            v = self.app.config.get(conf_key, None)
            if k not in kwargs:
                kwargs[k] = v
            elif overwrite and v:
                if isinstance(kwargs[k], dict):
                    kwargs[k].update(v)
                else:
                    kwargs[k] = v
        return kwargs

    def _generate_client_kwargs(self, name, kwargs):
        fetch_token = kwargs.pop('fetch_token', None)
        if fetch_token is None and self.fetch_token:
            fetch_token = functools.partial(self.fetch_token, name)
        if fetch_token:
            kwargs['fetch_token'] = fetch_token

        if kwargs['request_token_url']:
            return self._generate_oauth1_client_kwargs(name, kwargs)
        else:
            return self._generate_oauth2_client_kwargs(name, kwargs)

    def _generate_oauth1_client_kwargs(self, name, kwargs):
        cache = self.cache
        if not kwargs.get('fetch_request_token') and cache is not None:
            def fetch_request_token():
                key = _req_token_tpl.format(name)
                sid = session.pop(key, None)
                if not sid:
                    return None

                token = cache.get(sid)
                cache.delete(sid)
                return token

            kwargs['fetch_request_token'] = fetch_request_token

        if not kwargs.get('save_request_token') and cache is not None:
            def save_request_token(token):
                key = _req_token_tpl.format(name)
                sid = uuid.uuid4().hex
                session[key] = sid
                cache.set(sid, token, timeout=600)

            kwargs['save_request_token'] = save_request_token
        return kwargs

    def _generate_oauth2_client_kwargs(self, name, kwargs):
        update_token = kwargs.pop('update_token', None)
        if update_token is None and self.update_token:
            update_token = functools.partial(self.update_token, name)
        if update_token:
            kwargs['update_token'] = update_token
        return kwargs

    def __getattr__(self, key):
        try:
            return object.__getattribute__(self, key)
        except AttributeError:
            if key in self._registry:
                return self.create_client(key)
            raise AttributeError('No such client: %s' % key)
