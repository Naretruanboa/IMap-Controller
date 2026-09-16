from PIL import Image

from scripts.auto_label import generate_labels
from services.training_service import AITrainingService


def dataset(tmp_path):
    root = tmp_path / 'dataset'
    (root / 'images').mkdir(parents=True)
    (root / 'labels').mkdir()
    Image.new('RGB', (40, 40)).save(root / 'images' / 'gym.png')
    label = root / 'labels' / 'gym.txt'
    label.write_text('3 0.5 0.5 0.2 0.4\n')
    config = root / 'data.yaml'
    config.write_text('train: reviewed/train\nval: reviewed/val\n')
    return root, label, config


def test_service_keeps_reviewed_gym_and_dataset_split(tmp_path):
    root, label, config = dataset(tmp_path)
    result = AITrainingService(tmp_path).auto_label()
    assert result['skipped_images'] == 1
    assert result['labeled_images'] == 0
    assert label.read_text() == '3 0.5 0.5 0.2 0.4\n'
    assert config.read_text() == 'train: reviewed/train\nval: reviewed/val\n'


def test_cli_keeps_reviewed_gym_and_dataset_split(tmp_path):
    root, label, config = dataset(tmp_path)
    generate_labels(root / 'images', root / 'labels')
    assert label.read_text() == '3 0.5 0.5 0.2 0.4\n'
    assert config.read_text() == 'train: reviewed/train\nval: reviewed/val\n'
