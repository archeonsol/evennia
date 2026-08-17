import threading

from django.db import close_old_connections, models
from django.test import TestCase, TransactionTestCase

from .models import SharedMemoryModel


class Category(SharedMemoryModel):
    name = models.CharField(max_length=32)
    post_loads = 0

    def at_post_load(self):
        type(self).post_loads += 1


class RegularCategory(models.Model):
    name = models.CharField(max_length=32)


class Article(SharedMemoryModel):
    name = models.CharField(max_length=32)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    category2 = models.ForeignKey(RegularCategory, on_delete=models.CASCADE)


class RegularArticle(models.Model):
    name = models.CharField(max_length=32)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    category2 = models.ForeignKey(RegularCategory, on_delete=models.CASCADE)


class SharedMemorysTest(TestCase):
    # TODO: test for cross model relation (singleton to regular)

    def setUp(self):
        super().setUp()
        n = 0
        category = Category.objects.create(name="Category %d" % (n,))
        regcategory = RegularCategory.objects.create(name="Category %d" % (n,))

        for n in range(0, 10):
            Article.objects.create(
                name="Article %d" % (n,), category=category, category2=regcategory
            )
            RegularArticle.objects.create(
                name="Article %d" % (n,), category=category, category2=regcategory
            )

    def testSharedMemoryReferences(self):
        article_list = Article.objects.all().select_related("category")
        last_article = article_list[0]
        for article in article_list[1:]:
            self.assertEqual(article.category is last_article.category, True)
            last_article = article

    def testRegularReferences(self):
        article_list = RegularArticle.objects.all().select_related("category")
        last_article = article_list[0]
        for article in article_list[1:]:
            self.assertEqual(article.category2 is last_article.category2, False)
            last_article = article

    def testMixedReferences(self):
        article_list = RegularArticle.objects.all().select_related("category")
        last_article = article_list[0]
        for article in article_list[1:]:
            self.assertEqual(article.category is last_article.category, True)
            last_article = article

        # article_list = Article.objects.all().select_related('category')
        # last_article = article_list[0]
        # for article in article_list[1:]:
        #    self.assertEqual(article.category2 is last_article.category2, False)
        #    last_article = article

    def testObjectDeletion(self):
        # This must execute first so its guaranteed to be in memory.
        list(Article.objects.all().select_related("category"))

        article = Article.objects.all()[0:1].get()
        pk = article.pk
        article.delete()
        self.assertEqual(pk not in Article.__instance_cache__, True)


class SharedMemoryOwnershipTest(TransactionTestCase):
    """Canonical identity and persistence stay on the runtime owner."""

    def setUp(self):
        super().setUp()
        Category.post_loads = 0
        self.category = Category.objects.create(name="owner")
        self.regular_category = RegularCategory.objects.create(name="regular")
        self.article = Article.objects.create(
            name="article",
            category=self.category,
            category2=self.regular_category,
        )
        Category.post_loads = 0

    def tearDown(self):
        close_old_connections()
        Article.flush_instance_cache(force=True)
        Category.flush_instance_cache(force=True)
        super().tearDown()

    def _in_worker(self, operation):
        result = {}

        def run():
            close_old_connections()
            try:
                result["value"] = operation()
            except Exception as err:
                result["error"] = err
            finally:
                close_old_connections()

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        return result

    def test_warm_owner_instance_is_not_returned_to_worker(self):
        result = self._in_worker(lambda: Category.objects.get(pk=self.category.pk))

        self.assertNotIn("error", result)
        self.assertIsNot(result["value"], self.category)
        self.assertIs(Category.get_cached_instance(self.category.pk), self.category)
        self.assertEqual(Category.post_loads, 0)

    def test_cold_worker_does_not_cache_or_suppress_later_owner_hook(self):
        pk = self.category.pk
        Category.flush_instance_cache(force=True)

        result = self._in_worker(lambda: Category.objects.get(pk=pk))

        self.assertNotIn("error", result)
        self.assertIsNone(Category.get_cached_instance(pk))
        self.assertEqual(Category.post_loads, 0)
        owner = Category.objects.get(pk=pk)
        self.assertIs(Category.get_cached_instance(pk), owner)
        self.assertEqual(Category.post_loads, 1)

    def test_select_related_rows_are_detached_off_owner(self):
        result = self._in_worker(
            lambda: Article.objects.select_related("category").get(pk=self.article.pk)
        )

        self.assertNotIn("error", result)
        detached = result["value"]
        self.assertIsNot(detached, self.article)
        self.assertIsNot(detached.category, self.category)
        self.assertIs(Article.get_cached_instance(self.article.pk), self.article)
        self.assertIs(Category.get_cached_instance(self.category.pk), self.category)
        self.assertEqual(Category.post_loads, 0)

    def test_public_cache_apis_reject_off_owner(self):
        operations = (
            lambda: Category.cache_instance(self.category),
            Category.get_all_cached_instances,
            lambda: Category.flush_cached_instance(self.category),
            lambda: Category.flush_instance_cache(force=True),
            lambda: self.category.flush_from_cache(force=True),
        )

        for operation in operations:
            result = self._in_worker(operation)
            self.assertIn("OwnershipError", type(result["error"]).__name__)

        self.assertIs(Category.get_cached_instance(self.category.pk), self.category)

        from evennia.utils.idmapper.models import flush_cache

        result = self._in_worker(flush_cache)
        self.assertIn("OwnershipError", type(result["error"]).__name__)

    def test_off_owner_save_and_delete_fail_before_database_change(self):
        pk = self.category.pk

        result = self._in_worker(lambda: Category(name="worker-create").save())
        self.assertIn("OwnershipError", type(result["error"]).__name__)
        self.assertFalse(Category._base_manager.filter(name="worker-create").exists())

        def mutate():
            detached = Category.objects.get(pk=pk)
            detached.name = "worker"
            detached.save()

        result = self._in_worker(mutate)
        self.assertIn("OwnershipError", type(result["error"]).__name__)
        self.assertEqual(Category._base_manager.get(pk=pk).name, "owner")

        result = self._in_worker(lambda: Category.objects.get(pk=pk).delete())
        self.assertIn("OwnershipError", type(result["error"]).__name__)
        self.assertTrue(Category._base_manager.filter(pk=pk).exists())
        self.assertIs(Category.get_cached_instance(pk), self.category)

    def test_all_manager_paths_guard_queryset_mutation(self):
        from evennia.accounts.models import AccountDB
        from evennia.comms.models import ChannelDB, Msg
        from evennia.help.models import HelpEntry
        from evennia.objects.models import ObjectDB
        from evennia.scripts.models import ScriptDB
        from evennia.server.models import ServerConfig
        from evennia.utils.idmapper.manager import SharedMemoryQuerySet

        for model in (AccountDB, ObjectDB, ScriptDB, ChannelDB, Msg, HelpEntry, ServerConfig):
            self.assertIsInstance(model.objects.all(), SharedMemoryQuerySet)
            self.assertIsInstance(model._default_manager.all(), SharedMemoryQuerySet)
            self.assertIsInstance(model._base_manager.all(), SharedMemoryQuerySet)
        managers = (
            Category.objects,
            Category._default_manager,
            Category._base_manager,
            Category.objects.using("default"),
        )
        for manager in managers:
            result = self._in_worker(
                lambda manager=manager: manager.filter(pk=self.category.pk).delete()
            )
            self.assertIn("OwnershipError", type(result["error"]).__name__)
        self.assertTrue(Category._base_manager.filter(pk=self.category.pk).exists())

    def test_bulk_create_and_update_are_guarded(self):
        create_candidate = Category(name="worker-create")
        result = self._in_worker(lambda: Category.objects.bulk_create([create_candidate]))
        self.assertIn("OwnershipError", type(result["error"]).__name__)
        self.assertIsNone(create_candidate.pk)
        self.assertTrue(create_candidate._state.adding)
        self.assertFalse(Category._base_manager.filter(name="worker-create").exists())

        candidate = Category(id=self.category.pk, name="worker-update")
        state = (candidate.pk, candidate.name, candidate._state.adding)
        result = self._in_worker(lambda: Category._base_manager.bulk_update([candidate], ["name"]))
        self.assertIn("OwnershipError", type(result["error"]).__name__)
        self.assertEqual((candidate.pk, candidate.name, candidate._state.adding), state)
        self.assertEqual(Category._base_manager.get(pk=self.category.pk).name, "owner")

    def test_scalar_queryset_update_remains_available(self):
        updated = Category.objects.filter(pk=self.category.pk).update(name="scalar")
        Category.flush_cached_instance(self.category)

        self.assertEqual(updated, 1)
        self.assertEqual(Category._base_manager.get(pk=self.category.pk).name, "scalar")
