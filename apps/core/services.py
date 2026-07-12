class BaseService:
    """
    Base class for application services (the Service Layer from Volume 1,
    Section 1.4). Views and viewsets call services; services orchestrate
    repositories and the ML/validation layer. Business logic lives here,
    not in views - this is what keeps Volume 13's tests able to exercise
    logic without spinning up HTTP.
    """

    pass
