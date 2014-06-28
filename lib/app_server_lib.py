# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
#                         Copyright (C) 2014 Chuan Ji                         #
#                             All Rights Reserved                             #
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
"""Main web app server library."""

import flask
from flask.ext import login
import functools
import json
import logging
import mongoengine

from lib import auth_lib
from lib import asciidoc_lib
from lib import env_lib
from lib import models_lib


# The WSGI app object.
# pylint: disable=invalid-name
app = flask.Flask(
    __name__,
    template_folder=env_lib.TEMPLATES_DIR,
    static_folder=env_lib.STATIC_DIR)
app.config.from_object(env_lib.CONFIG)
env_lib.DB.init_app(app)
env_lib.LOGIN_MANAGER.init_app(app)
# pylint: enable=invalid-name


# Some stock responses.
_SUCCESS_RESPONSE = {
    'success': True,
}
_INVALID_REQUEST_RESPONSE = {
    'success': False,
    'error_message': 'Invalid request',
}
_NOT_AUTHENTICATED_RESPONSE = {
    'success': False,
    'error_message': 'Not authenticated',
}
_INVALID_DOCUMENT_ID_RESPONSE = {
    'success': False,
    'error_message': 'Invalid document ID',
}


def _JsonView(view_function):
  """A decorator for a Flask view function that returns a JSON response.

  This will also pass flask.request.get_json() to the wrapped method as the
  first argument.
  """

  @functools.wraps(view_function)
  def WrappedViewFunction(*args, **kwargs):
    return json.dumps(view_function(flask.request.get_json(), *args, **kwargs))

  return WrappedViewFunction


def _RequireAuth(view_function):
  """A decorator for a _JsonView that checks for authentication.

  If the user is not logged in, returns _NOT_AUTHENTICATED_RESPONSE.
  """

  @functools.wraps(view_function)
  def WrappedViewFunction(*args, **kwargs):
    if not login.current_user.is_authenticated():
      return _NOT_AUTHENTICATED_RESPONSE
    return view_function(*args, **kwargs)

  return WrappedViewFunction


def _RequireText(view_function):
  """A decorator for a _JsonView that checks for a valid "text" parameter.

  If the "text" parameter is not present, or is longer than
  env_lib.MAX_SOURCE_TEXT_SIZE, returns _INVALID_REQUEST_RESPONSE.
  """

  @functools.wraps(view_function)
  def WrappedViewFunction(request_data, *args, **kwargs):
    if ('text' not in request_data or
        len(request_data['text']) > env_lib.MAX_SOURCE_TEXT_SIZE):
      return _INVALID_REQUEST_RESPONSE
    return view_function(request_data, *args, **kwargs)

  return WrappedViewFunction


def _RenderTemplate(template, extra_args=None):
  """Renders a template with the default args and optional extra args."""
  args = {
      'user': login.current_user,
  }
  if extra_args:
    args.update(extra_args)
  return flask.render_template(template, **args).strip()


@app.route('/')
def RenderRoot():
  """Handler for the root URI."""
  if '_escaped_fragment_' in flask.request.args:
    # Static version for search engine crawlers.
    return _RenderTemplate('static.html')

  if login.current_user.is_authenticated():
    return flask.redirect(flask.url_for('.RenderDocumentList'))

  return flask.redirect(flask.url_for('.RenderScratchEditor'))


@app.route('/scratch')
def RenderScratchEditor():
  """Handler for rendering an unsaved document editor."""
  if 'new' in flask.request.args:
    default_text = ''
  else:
    default_text = _RenderTemplate('intro.txt')
  if 'header' in flask.request.args:
    template = 'editor_header.html'
  else:
    template = 'editor.html'
  return _RenderTemplate(template, {
      'document': None,
      'asciidoc_editor_text': default_text,
  })



@app.route('/d/<document_id>')
@login.login_required
def RenderEditorForDocument(document_id):
  """Handler for the main editor page with a previously saved document."""
  document = models_lib.UserDocument.Get(document_id)
  if document is None or not document.IsReadableByUser(login.current_user):
    return flask.redirect(flask.url_for('.RenderRoot'))
  if 'header' in flask.request.args:
    template = 'editor_header.html'
  else:
    template = 'editor.html'
  return _RenderTemplate(template, {'document': document})


@app.route('/home')
@login.login_required
def RenderDocumentList():
  """Handler for the document list page."""
  documents = login.current_user.GetOwnedDocuments()
  return _RenderTemplate('document_list.html', {'documents': documents})


@app.route('/sitemap.xml')
def RenderSitemap():
  """Handler for a sitemap request."""
  return _RenderTemplate('sitemap.xml')


@app.route('/api/v1/asciidoc-to-html', methods=['POST'])
@_JsonView
@_RequireText
def AsciiDocToHtml(request_data):
  """Handler for AsciiDoc to HTML conversion.

  POST data: A JSON object:
    - text: the text to convert.
  Returns:
    A JSON objct:
      - success: a boolean.
      - html: HTML generated by AsciiDoc.
      - error_message: warnings or error messages.
  """
  (return_code, stdout_output, stderr_output) = (
      asciidoc_lib.GetAsciiDocResult(request_data['text']))
  return {
      'success': return_code == 0,
      'html': stdout_output,
      'error_message': stderr_output,
  }


@app.route('/api/v1/auth', methods=['POST'])
@_JsonView
def Auth(request_data):
  """Handler for user authentication.

  This is invoked by the client side after a successful login.

  POST data: a JSON object:
    - accounts: an array of objects:
      - account_provider_type: type of the account provider.
      - user_id: the user's ID with the account provider.
      - auth_token: the authentication token obtained from the provider.
      - data: extra data for this user obtained from the account provider. The
          format depends on the account provider.
  Returns:
    A JSON objct:
      - success: a boolean.
      - user_id: our site's user ID.
      - error_message: warnings or error messages.
  """
  if ('accounts' not in request_data or
      not isinstance(request_data['accounts'], list) or
      not request_data['accounts']):
    return _INVALID_REQUEST_RESPONSE
  logging.debug('Auth request: %s', str(request_data))
  accounts = []
  for account_data in request_data['accounts']:
    if (not account_data.get('account_provider_type', None) or
        not account_data.get('user_id', None) or
        not account_data.get('auth_token', None) or
        account_data['account_provider_type'] not in
        auth_lib.ACCOUNT_PROVIDERS):
      return _INVALID_REQUEST_RESPONSE
    account_provider = (
        auth_lib.ACCOUNT_PROVIDERS[account_data['account_provider_type']])
    if not account_provider.IsTokenValid(
        account_data['user_id'], account_data['auth_token']):
      return {
          'success': False,
          'error_message': 'Invalid token',
      }
    accounts.append(
        (account_data['account_provider_type'],
         account_data['user_id'],
         account_data['data']))
  # All accounts valid.
  user = auth_lib.FindOrCreateUser(accounts)
  login.login_user(user, remember=True)
  return {
      'success': True,
      'user_id': user.user_id,
  }


@app.route('/api/v1/logout', methods=['POST'])
@_JsonView
@_RequireAuth
def Logout(_):
  """Handler for user logout.

  POST data: an empty JSON object.
  Returns:
    A JSON objct:
      - success: a boolean.
      - error_message: warnings or error messages.
  """
  login.logout_user()
  return _SUCCESS_RESPONSE


@app.route('/api/v1/documents', methods=['PUT'])
@_JsonView
@_RequireAuth
@_RequireText
def CreateDocument(request_data):
  """Saves a new document.

  Login required.

  POST data: a JSON object:
    - title: the title of the document (optional).
    - text: the source text.
    - visibility: the visibility of the document, as a string.
  Returns:
    A JSON objct:
      - success: a boolean.
      - error_message: warnings or error messages.
      - document_id: ID of the created document.
  """
  document = models_lib.UserDocument()
  document.owner = login.current_user._get_current_object()
  document.FromJson(request_data)
  while True:
    document.document_id = models_lib.UserDocument.NewDocumentId()
    try:
      document.save()
    except mongoengine.errors.NotUniqueError:
      pass
    except mongoengine.errors.ValidationError:
      return _INVALID_REQUEST_RESPONSE
    else:
      break
  return {
      'success': True,
      'document_id': document.document_id,
  }


@app.route('/api/v1/documents/<document_id>', methods=['POST'])
@_JsonView
@_RequireAuth
def GetDocument(_, document_id):
  document = models_lib.UserDocument.Get(document_id)
  if not document:
    return _INVALID_DOCUMENT_ID_RESPONSE
  if not document.IsReadableByUser(login.current_user):
    return _NOT_AUTHENTICATED_RESPONSE
  response = document.toJson()
  response.update({
      'success': True,
  })
  return response



@app.route('/api/v1/documents/<document_id>', methods=['POST'])
@_JsonView
@_RequireAuth
@_RequireText
def SaveDocument(request_data, document_id):
  """Updates an existing document.

  Login required.

  POST data: a JSON object:
    - title: the title of the document (optional).
    - text: the source text.
    - visibility: the visibility of the document, as a string.
  Returns:
    A JSON objct:
      - success: a boolean.
      - error_message: warnings or error messages.
  """
  document = models_lib.UserDocument.Get(document_id)
  if not document:
    return _INVALID_DOCUMENT_ID_RESPONSE
  if not document.IsWritableByUser(login.current_user):
    return _NOT_AUTHENTICATED_RESPONSE
  document.FromJson(request_data)
  try:
    document.save()
  except mongoengine.errors.ValidationError:
    return _INVALID_REQUEST_RESPONSE
  return _SUCCESS_RESPONSE


@app.route('/api/v1/documents/<document_id>', methods=['DELETE'])
@_JsonView
@_RequireAuth
def DeleteDocument(_, document_id):
  """Deletes a document.

  Login required.

  POST data: None.
  Returns:
    A JSON objct:
      - success: a boolean.
      - error_message: warnings or error messages.
  """
  document = models_lib.UserDocument.Get(document_id)
  if not document:
    return _INVALID_DOCUMENT_ID_RESPONSE
  if not document.IsWritableByUser(login.current_user):
    return _NOT_AUTHENTICATED_RESPONSE
  document.delete()
  return _SUCCESS_RESPONSE
