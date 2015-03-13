from django.db import models
from django.conf import settings

import uuid
import basic_models
import cachemodel
from autoslug import AutoSlugField
import django.template.loader
from jsonfield import JSONField

from badgeanalysis.models import OpenBadge
from badgeanalysis.utils import test_probable_url
from badgeanalysis.scheme_models import BadgeScheme


class AbstractBadgeObject(cachemodel.CacheModel):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(getattr(settings, 'AUTH_USER_MODEL'), blank=True, null=True)

    badge_object = JSONField()

    class Meta:
        abstract = True

    def get_full_url(self):
        return str(getattr(settings, 'HTTP_ORIGIN')) +  self.get_absolute_url()

class Issuer(AbstractBadgeObject):
    name = models.CharField(max_length=1024)
    slug = AutoSlugField(max_length=255, populate_from='name', unique=True, blank=False, editable=True)

    owner = models.ForeignKey(getattr(settings, 'AUTH_USER_MODEL'), related_name='owner', on_delete=models.PROTECT, null=False)
    # editors may define badgeclasses and issue badges
    editors = models.ManyToManyField(getattr(settings, 'AUTH_USER_MODEL'), db_table='issuer_editors', related_name='issuers_editor_for')
    # staff may issue badges from badgeclasses that already exist
    staff = models.ManyToManyField(getattr(settings, 'AUTH_USER_MODEL'), db_table='issuer_staff', related_name='issuers_staff_for')

    image = models.ImageField(upload_to='uploads/issuers', blank=True)

    def get_absolute_url(self):
        return "/public/issuers/%s" % self.slug

    def save(self):
        super(Issuer, self).save()
        object_id = self.badge_object.get('@id')
        if object_id != self.get_full_url():
            self.badge_object['@id'] = self.get_full_url()
            super(Issuer, self).save()


class IssuerBadgeClass(AbstractBadgeObject):
    issuer = models.ForeignKey(Issuer, blank=False, null=False, on_delete=models.PROTECT, related_name="badgeclasses")
    name = models.CharField(max_length=255)
    slug = AutoSlugField(max_length=255, populate_from='name', unique=True, blank=False, editable=True)
    criteria_text = models.TextField(blank=True, null=True)  # TODO: refactor to be a rich text field via ckeditor
    criteria_url = models.URLField(max_length=1024, blank=True, null=True)
    image = models.ImageField(upload_to='uploads/badges', blank=True)

    @property
    def owner(self):
        return self.obi_issuer.owner

    # criteria may either be locally hosted text or a remote UR
    @property
    def criteria(self):
        if self.criteria_url is not None:
            return self.criteria_url
        else:
            return self.criteria_text
    @criteria.setter
    def criteria(self, value):
        if test_probable_url(value):
            self.criteria_url = value
            self.criteria_text = None
        else:
            self.criteria_url = None
            self.criteria_text = value

    def get_absolute_url(self):
        return "/public/badges/%s" % self.slug




class IssuerAssertion(AbstractBadgeObject):
    badgeclass = models.ForeignKey(IssuerBadgeClass, blank=False, null=False, on_delete=models.PROTECT)

    # in the future, obi_issuer might be different from badgeclass.obi_issuer sometimes
    issuer = models.ForeignKey(Issuer, blank=False, null=False)
    slug = AutoSlugField(max_length=255, populate_from='get_new_slug', unique=True, blank=False, editable=False)

    @property
    def owner(self):
        return self.obi_issuer.owner

    def get_absolute_url(self):
        return "/public/assertions/%s" % self.slug

    def get_new_slug(self):
        return str(uuid.uuid4())


class EarnerNotification(basic_models.TimestampedModel):
    url = models.URLField(verbose_name='Assertion URL', max_length=2048)
    email = models.EmailField(max_length=254, blank=False)
    badge = models.ForeignKey(OpenBadge, blank=True, null=True)

    def get_form(self):
        from issuer.forms import NotifyEarnerForm
        return NotifyEarnerForm(instance=self)

    @classmethod
    def detect_existing(cls, url):
        try:
            cls.objects.get(url=url)
        except EarnerNotification.DoesNotExist:
            return False
        except EarnerNotification.MultipleObjectsReturned:
            return False
        else:
            return True

    def send_email(self):
        http_origin = getattr(settings, 'HTTP_ORIGIN', None)
        ob = self.badge
        email_context = {
            'badge_name': ob.ldProp('bc', 'name'),
            'badge_description': ob.ldProp('bc', 'description'),
            'issuer_name': ob.ldProp('iss', 'name'),
            'issuer_url': ob.ldProp('iss', 'url'),
            'image_url': ob.get_baked_image_url(**{'origin': http_origin})
        }
        t = django.template.loader.get_template('issuer/notify_earner_email.txt')
        ht = django.template.loader.get_template('issuer/notify_earner_email.html')
        text_output_message = t.render(email_context)
        html_output_message = ht.render(email_context)
        mail_meta = {
            'subject': 'Congratulations, you earned a badge!',
            # 'from_address': email_context['issuer_name'] + ' Badges <noreply@oregonbadgealliance.org>',
            'from_address': 'Oregon Badge Alliance' + ' Badges <noreply@oregonbadgealliance.org>',
            'to_addresses': [self.email]
        }

        try:
            from django.core.mail import send_mail
            send_mail(
                mail_meta['subject'],
                text_output_message,
                mail_meta['from_address'],
                mail_meta['to_addresses'],
                fail_silently=False,
                html_message=html_output_message
            )
        except Exception as e:
            raise e
