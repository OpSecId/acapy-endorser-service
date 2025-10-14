"""APIRouter module for managing endorser configurations in an async FastAPI context."""

import logging
from codecs import iterdecode
from csv import DictReader
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from api.endpoints.dependencies.db import get_db
from api.endpoints.models.configurations import ConfigurationType
from api.services.admin import (
    get_endorser_configs,
    get_endorser_config,
    validate_endorser_config,
    update_endorser_config,
)
from api.endpoints.models.configurations import (
    Configuration,
)
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR
from fastapi.security import OAuth2PasswordBearer
from api.services.allow_lists import add_to_allow_list, updated_allowed
from api.db.errors import AlreadyExists
from api.db.models.allow import (
    AllowedCredentialDefinition,
    AllowedSchema,
    AllowedLogEntry,
    AllowedPublicDid,
)
from api.db.models.base import BaseModel
from sqlalchemy.exc import IntegrityError


logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"], dependencies=[Depends(OAuth2PasswordBearer(tokenUrl="token"))])


def db_to_http_exception(e: Exception) -> int:
    """Convert database exceptions to HTTP status codes."""
    match e:
        case IntegrityError():
            return HTTP_500_INTERNAL_SERVER_ERROR
        case AlreadyExists():
            return HTTP_500_INTERNAL_SERVER_ERROR
        case _:
            return HTTP_500_INTERNAL_SERVER_ERROR


def maybe_str_to_bool(s: str) -> str | bool:
    """Convert string to boolean if applicable."""
    return s == "True" if isinstance(s, str) else s


def construct_allowed_credential_definition(cd: dict) -> AllowedCredentialDefinition:
    """Construct AllowedCredentialDefinition with proper boolean conversion."""
    cd["rev_reg_def"] = maybe_str_to_bool(cd["rev_reg_def"])
    cd["rev_reg_entry"] = maybe_str_to_bool(cd["rev_reg_entry"])
    ncd = AllowedCredentialDefinition(**cd)
    return ncd


async def update_allowed_config(k, v, db):
    """Update the allowed configuration in the database with entries from a CSV file.

    This function reads a CSV file, constructs instances of specified classes,
    and adds them to the database.

    Args:
        k: An object with attributes 'file' (CSV file handle)
           and 'filename' (name of the CSV file).
        v: A class type, used to construct instances from CSV data.
        db: Database session to which the constructed instances are added.

    Returns:
        A dictionary containing the filename and list of constructed class instances.

    """
    csvReader = DictReader(iterdecode(k.file, "utf-8"))
    constructed_classes = [
        (
            construct_allowed_credential_definition(i)
            if v is AllowedCredentialDefinition
            else v(**i)
        )
        for i in csvReader
    ]
    tmp = {
        "file_name": k.filename,
        "contents": constructed_classes,
    }
    for i in tmp["contents"]:
        db.add(i)
    return tmp


async def update_full_config(
    log_entry: Optional[UploadFile],
    publish_did: Optional[UploadFile],
    schema: Optional[UploadFile],
    credential_definition: Optional[UploadFile],
    db: AsyncSession,
    delete_contents: bool,
) -> dict:
    """Update full configuration, possibly deleting existing entries."""
    correlated_tables = {
        log_entry: AllowedLogEntry,
        publish_did: AllowedPublicDid,
        schema: AllowedSchema,
        credential_definition: AllowedCredentialDefinition,
    }
    modifications = {}
    for k, v in correlated_tables.items():
        if k:
            if delete_contents:
                await db.execute(delete(v))
            modifications[v.__name__] = await update_allowed_config(k, v, db)
    await db.commit()
    await updated_allowed(db)
    return modifications


@router.get("/config", status_code=status.HTTP_200_OK, response_model=dict)
async def get_config(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Retrieve endorser configurations with optional sorting and paging.

    Args:
        db (AsyncSession): Database session dependency.

    Returns:
        dict: Endorser configurations.

    Raises:
        HTTPException: If an error occurs while retrieving configurations.
    """
    try:
        endorser_configs = await get_endorser_configs(db)
        return endorser_configs
    except Exception as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/config/{config_name}",
    status_code=status.HTTP_200_OK,
    response_model=Configuration,
)
async def get_config_by_name(
    config_name: str,
    db: AsyncSession = Depends(get_db),
) -> Configuration:
    """Retrieve an endorser configuration by name asynchronously."""
    # This should take some query params, sorting and paging params...
    try:
        endorser_config = await get_endorser_config(db, config_name)
        return endorser_config
    except Exception as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/config/{config_name}",
    status_code=status.HTTP_200_OK,
    response_model=Configuration,
)
async def update_config(
    config_name: str,
    config_value: str,
    db: AsyncSession = Depends(get_db),
) -> Configuration:
    """Update the endorser configuration for the given config name and value.

    Parameters:
        config_name (str): The name of the configuration to update.
        config_value (str): The new value for the configuration.
        db (AsyncSession): Database session dependency.

    Returns:
        Configuration: The updated configuration object.

    Raises:
        HTTPException: If an error occurs during the update process.
    """
    try:
        ConfigurationType[config_name]
        validate_endorser_config(config_name, config_value)
        endorser_config = await update_endorser_config(db, config_name, config_value)
        return endorser_config
    except Exception as e:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))



@router.post(
    "/config",
    status_code=status.HTTP_200_OK,
    response_model=dict,
    description="Upload a new CSV config replacing the existing configuration.",
)
async def set_config(
    log_entry: Annotated[
        UploadFile, File(description="List of log entries authorized to be published")
    ] = None,
    publish_did: Annotated[
        UploadFile, File(description="List of DIDs authorized to become public")
    ] = None,
    schema: Annotated[
        UploadFile, File(description="List of schemas authorized to be published")
    ] = None,
    credential_definition: Annotated[
        UploadFile, File(description="List of creddefs authorized to be published")
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set new configuration by uploading CSVs, replacing the existing configuration."""
    try:
        return await update_full_config(
            log_entry, publish_did, schema, credential_definition, db, True
        )
    except Exception as e:
        raise HTTPException(status_code=db_to_http_exception(e), detail=str(e))


@router.put(
    "/config",
    status_code=status.HTTP_200_OK,
    response_model=dict,
    description="Upload a new CSV config appending to the existing configuration.",
)
async def append_config(
    log_entry: Annotated[
        UploadFile, File(description="List of log entries authorized to be published")
    ] = None,
    publish_did: Annotated[
        UploadFile, File(description="List of DIDs authorized to become public")
    ] = None,
    schema: Annotated[
        UploadFile, File(description="List of schemas authorized to be published")
    ] = None,
    credential_definition: Annotated[
        UploadFile, File(description="List of authorized creddefs")
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Append new configuration by uploading CSVs to the existing configuration."""
    try:
        return await update_full_config(
            log_entry, publish_did, schema, credential_definition, db, False
        )
    except Exception as e:
        raise HTTPException(status_code=db_to_http_exception(e), detail=str(e))